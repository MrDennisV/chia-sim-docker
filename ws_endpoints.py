"""
WebSocket service for the Chia simulator.

Exposes a JSON-RPC 2.0 interface over /ws with:
  * RPC passthrough to the full node (same coinset-compatible method set as REST)
  * Subscriptions for block peak changes, per-coin events, per-puzzle-hash events
  * A single server-side poller (1 req/s to the node) broadcasts events to subscribers

Reconnect pattern: stateless. Clients resubscribe after reconnect; the subscribe
response returns the current state so nothing is "missed" on resume.
"""
import asyncio
import contextlib
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable, Iterable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger("ws")

# Methods exposed via WS passthrough — coinset-compatible read/write only.
# Simulator-specific (farm_block, fund_wallet, revert_blocks, set_config, v1/*)
# remain REST-only by design.
WS_RPC_METHODS: frozenset[str] = frozenset({
    "get_blockchain_state", "get_network_info", "get_routes",
    "get_block", "get_blocks", "get_block_record", "get_block_record_by_height",
    "get_block_records", "get_block_spends",
    "get_coin_record_by_name", "get_coin_records_by_names",
    "get_coin_records_by_puzzle_hash", "get_coin_records_by_puzzle_hashes",
    "get_coin_records_by_parent_ids", "get_coin_records_by_hint",
    "get_puzzle_and_solution", "get_additions_and_removals",
    "get_all_mempool_tx_ids", "get_all_mempool_items",
    "get_mempool_item_by_tx_id", "get_mempool_items_by_coin_name",
    "get_fee_estimate", "push_tx",
})

SUBSCRIBE_METHODS: frozenset[str] = frozenset({
    "subscribe_block", "unsubscribe_block",
    "subscribe_coins", "unsubscribe_coins",
    "subscribe_puzzle_hashes", "unsubscribe_puzzle_hashes",
})


def _hexnorm(s: str) -> str:
    """Normalize hex input: lowercase, always 0x-prefixed."""
    if not isinstance(s, str):
        raise ValueError("hex string required")
    s = s.strip().lower()
    if not s.startswith("0x"):
        s = "0x" + s
    # Validate
    int(s, 16)
    return s


def _compute_coin_id(coin: dict) -> str:
    """Chia coin id = sha256(parent_coin_info || puzzle_hash || int_to_bytes(amount))."""
    parent = bytes.fromhex(coin["parent_coin_info"].removeprefix("0x"))
    ph = bytes.fromhex(coin["puzzle_hash"].removeprefix("0x"))
    amount = int(coin["amount"])
    if amount == 0:
        amount_bytes = b""
    else:
        byte_count = (amount.bit_length() + 8) // 8
        amount_bytes = amount.to_bytes(byte_count, "big", signed=True)
    return "0x" + hashlib.sha256(parent + ph + amount_bytes).hexdigest()


def _coins_touched_by_mempool_item(item: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Return (removed_coin_ids, created_coins) where created_coins is a list of
    (coin_id, puzzle_hash) for additions implied by the item's coin_spends.
    Additions are derived best-effort from spend_bundle when present.
    """
    removed: list[str] = []
    created: list[tuple[str, str]] = []
    spend_bundle = item.get("spend_bundle") or {}
    for spend in spend_bundle.get("coin_spends", []) or []:
        coin = spend.get("coin") or {}
        if coin.get("parent_coin_info") and coin.get("puzzle_hash") is not None:
            try:
                removed.append(_compute_coin_id(coin))
            except Exception as e:
                log.debug(f"coin_id from spend failed: {e}")
    # Mempool items may also expose a `additions` list in some node versions
    for add in item.get("additions") or []:
        coin = add if "parent_coin_info" in add else add.get("coin") or {}
        ph = coin.get("puzzle_hash")
        if coin.get("parent_coin_info") and ph:
            try:
                created.append((_compute_coin_id(coin), _hexnorm(ph)))
            except Exception as e:
                log.debug(f"coin_id from addition failed: {e}")
    return removed, created


class Subscription:
    __slots__ = ("ws", "block", "coin_ids", "puzzle_hashes", "send_lock")

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.block: bool = False
        self.coin_ids: set[str] = set()
        self.puzzle_hashes: set[str] = set()
        self.send_lock = asyncio.Lock()

    async def send(self, message: dict) -> bool:
        """Send a JSON message. Returns False if the socket is gone."""
        try:
            async with self.send_lock:
                await self.ws.send_text(json.dumps(message))
            return True
        except Exception as e:
            log.debug(f"ws send failed: {e}")
            return False


class Registry:
    def __init__(self):
        self._subs: set[Subscription] = set()
        self._lock = asyncio.Lock()

    async def add(self, sub: Subscription) -> None:
        async with self._lock:
            self._subs.add(sub)

    async def remove(self, sub: Subscription) -> None:
        async with self._lock:
            self._subs.discard(sub)

    def snapshot(self) -> list[Subscription]:
        return list(self._subs)

    def block_subscribers(self) -> list[Subscription]:
        return [s for s in self._subs if s.block]

    def coin_subscribers(self, coin_id: str) -> list[Subscription]:
        return [s for s in self._subs if coin_id in s.coin_ids]

    def puzzle_hash_subscribers(self, ph: str) -> list[Subscription]:
        return [s for s in self._subs if ph in s.puzzle_hashes]


async def _broadcast(subs: Iterable[Subscription], payload: dict, registry: Registry) -> None:
    """Send payload to each subscription. Drop any that fail."""
    for sub in subs:
        ok = await sub.send(payload)
        if not ok:
            await registry.remove(sub)


def _event(channel: str, data: dict) -> dict:
    return {"jsonrpc": "2.0", "method": "event", "params": {"channel": channel, "data": data}}


class Poller:
    """
    Single background task: polls the node every `interval` seconds and
    broadcasts events to subscribers.

    Per tick:
      1. get_blockchain_state -> on peak advance, for each new block call
         get_block_record_by_height + get_additions_and_removals and emit
         block.peak / coin.spent / coin.created events.
      2. get_all_mempool_items -> diff vs previous snapshot, emit
         coin.mempool.in / coin.mempool.out.
    """

    def __init__(
        self,
        rpc_fn: Callable[..., Any],
        registry: Registry,
        interval: float = 1.0,
        rpc_timeout: float = 5.0,
    ):
        self.rpc = rpc_fn
        self.registry = registry
        self.interval = interval
        self.rpc_timeout = rpc_timeout
        self._task: asyncio.Task | None = None
        self._running = False
        # Snapshot state
        self.last_height: int = -1
        self.last_header_hash: str | None = None
        self.last_mempool_bundles: set[str] = set()
        self.last_mempool_items: dict[str, dict] = {}

    async def _call(self, method: str, params: dict | None = None) -> dict:
        return await asyncio.wait_for(
            asyncio.to_thread(self.rpc, method, params or {}),
            timeout=self.rpc_timeout,
        )

    async def current_state(self) -> dict:
        """Snapshot for new subscribers. Best-effort; returns {} on failure."""
        try:
            d = await self._call("get_blockchain_state")
            peak = (d.get("blockchain_state") or {}).get("peak") or {}
            return {
                "height": peak.get("height", 0),
                "header_hash": peak.get("header_hash"),
                "timestamp": peak.get("timestamp"),
            }
        except Exception:
            return {}

    async def coin_state(self, coin_id: str) -> dict:
        """Current on-chain + mempool status for a coin. For subscribe responses."""
        out: dict[str, Any] = {"coin_id": coin_id, "on_chain": None, "in_mempool": []}
        try:
            rec = await self._call("get_coin_record_by_name", {"name": coin_id})
            cr = rec.get("coin_record")
            if cr:
                out["on_chain"] = {
                    "spent": bool(cr.get("spent")),
                    "spent_height": cr.get("spent_block_index", 0) or None,
                    "confirmed_height": cr.get("confirmed_block_index", 0),
                    "coin": cr.get("coin"),
                }
        except Exception as e:
            log.debug(f"coin_state on_chain for {coin_id}: {e}")
        try:
            mem = await self._call("get_mempool_items_by_coin_name", {"coin_name": coin_id})
            items = mem.get("mempool_items") or []
            out["in_mempool"] = [{"mempool_item_id": (it.get("spend_bundle_name") or "")} for it in items]
        except Exception as e:
            log.debug(f"coin_state mempool for {coin_id}: {e}")
        return out

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Initial snapshot so the first tick doesn't produce spurious "new" events
        try:
            state = await self._call("get_blockchain_state")
            peak = (state.get("blockchain_state") or {}).get("peak") or {}
            self.last_height = peak.get("height", -1)
            self.last_header_hash = peak.get("header_hash")
        except Exception as e:
            log.warning(f"poller initial state failed: {e}")
            self.last_height = -1
        try:
            mp = await self._call("get_all_mempool_items")
            items = mp.get("mempool_items") or {}
            self.last_mempool_bundles = set(items.keys())
            self.last_mempool_items = dict(items)
        except Exception as e:
            log.warning(f"poller initial mempool failed: {e}")
        log.info(
            f"poller started: height={self.last_height} mempool_items={len(self.last_mempool_bundles)}"
        )
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            tick_start = time.monotonic()
            try:
                await self._tick_blocks()
                await self._tick_mempool()
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                log.warning("poller: RPC timeout, tick skipped")
            except Exception as e:
                log.warning(f"poller tick error: {type(e).__name__}: {e}")
            elapsed = time.monotonic() - tick_start
            try:
                await asyncio.sleep(max(0.05, self.interval - elapsed))
            except asyncio.CancelledError:
                break

    async def _tick_blocks(self) -> None:
        state = await self._call("get_blockchain_state")
        peak = (state.get("blockchain_state") or {}).get("peak") or {}
        new_height = peak.get("height", -1)
        if new_height <= self.last_height:
            return
        # Catch-up: process every block from last_height+1 to new_height
        for h in range(self.last_height + 1, new_height + 1):
            try:
                rec = await self._call("get_block_record_by_height", {"height": h})
                br = rec.get("block_record") or {}
                hh = br.get("header_hash")
                if not hh:
                    continue
                adds_rems = await self._call("get_additions_and_removals", {"header_hash": hh})
                await self._emit_block(h, hh, br.get("timestamp") or 0)
                await self._emit_block_coin_events(adds_rems, h)
            except Exception as e:
                log.warning(f"poller block {h}: {e}")
        self.last_height = new_height
        self.last_header_hash = peak.get("header_hash")

    async def _emit_block(self, height: int, header_hash: str, timestamp: int) -> None:
        payload = _event("block.peak", {
            "height": height,
            "header_hash": header_hash,
            "timestamp": timestamp,
        })
        await _broadcast(self.registry.block_subscribers(), payload, self.registry)

    async def _emit_block_coin_events(self, adds_rems: dict, height: int) -> None:
        for rem in adds_rems.get("removals") or []:
            coin = rem.get("coin") or {}
            try:
                cid = _compute_coin_id(coin)
            except Exception:
                continue
            ph = _hexnorm(coin.get("puzzle_hash", "")) if coin.get("puzzle_hash") else None
            payload = _event("coin.spent", {
                "coin_id": cid,
                "coin": coin,
                "spent_height": height,
            })
            targets: dict[int, Subscription] = {}
            for s in self.registry.coin_subscribers(cid):
                targets[id(s)] = s
            if ph:
                for s in self.registry.puzzle_hash_subscribers(ph):
                    targets[id(s)] = s
            if targets:
                await _broadcast(targets.values(), payload, self.registry)
        for add in adds_rems.get("additions") or []:
            coin = add.get("coin") or {}
            try:
                cid = _compute_coin_id(coin)
            except Exception:
                continue
            ph = _hexnorm(coin.get("puzzle_hash", "")) if coin.get("puzzle_hash") else None
            if not ph:
                continue
            subs = self.registry.puzzle_hash_subscribers(ph)
            if not subs:
                continue
            payload = _event("coin.created", {
                "coin_id": cid,
                "coin": coin,
                "confirmed_height": height,
            })
            await _broadcast(subs, payload, self.registry)

    async def _tick_mempool(self) -> None:
        mp = await self._call("get_all_mempool_items")
        items: dict = mp.get("mempool_items") or {}
        current = set(items.keys())
        entered = current - self.last_mempool_bundles
        exited = self.last_mempool_bundles - current
        for bid in entered:
            item = items.get(bid) or {}
            removed, _created = _coins_touched_by_mempool_item(item)
            for cid in removed:
                subs = self.registry.coin_subscribers(cid)
                if not subs:
                    continue
                payload = _event("coin.mempool.in", {
                    "coin_id": cid,
                    "mempool_item_id": bid,
                })
                await _broadcast(subs, payload, self.registry)
        for bid in exited:
            item = self.last_mempool_items.get(bid) or {}
            removed, _created = _coins_touched_by_mempool_item(item)
            for cid in removed:
                subs = self.registry.coin_subscribers(cid)
                if not subs:
                    continue
                payload = _event("coin.mempool.out", {
                    "coin_id": cid,
                    "mempool_item_id": bid,
                })
                await _broadcast(subs, payload, self.registry)
        self.last_mempool_bundles = current
        self.last_mempool_items = dict(items)


# ───────────────────────── WebSocket handler ─────────────────────────


def _err(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def _ok(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


async def _handle_subscribe(
    method: str, params: dict, sub: Subscription, poller: Poller
) -> dict:
    if method == "subscribe_block":
        sub.block = True
        return {"subscribed": "block", "current": await poller.current_state()}

    if method == "unsubscribe_block":
        sub.block = False
        return {"unsubscribed": "block"}

    if method == "subscribe_coins":
        raw = params.get("coin_ids") or []
        if not isinstance(raw, list) or not raw:
            raise ValueError("coin_ids (non-empty list) required")
        ids = [_hexnorm(x) for x in raw]
        sub.coin_ids.update(ids)
        current = {cid: await poller.coin_state(cid) for cid in ids}
        return {"subscribed": ids, "current": current}

    if method == "unsubscribe_coins":
        raw = params.get("coin_ids") or []
        ids = [_hexnorm(x) for x in raw] if raw else []
        if ids:
            sub.coin_ids.difference_update(ids)
        else:
            sub.coin_ids.clear()
        return {"unsubscribed": ids or "all", "remaining": sorted(sub.coin_ids)}

    if method == "subscribe_puzzle_hashes":
        raw = params.get("puzzle_hashes") or []
        if not isinstance(raw, list) or not raw:
            raise ValueError("puzzle_hashes (non-empty list) required")
        phs = [_hexnorm(x) for x in raw]
        sub.puzzle_hashes.update(phs)
        return {"subscribed": phs}

    if method == "unsubscribe_puzzle_hashes":
        raw = params.get("puzzle_hashes") or []
        phs = [_hexnorm(x) for x in raw] if raw else []
        if phs:
            sub.puzzle_hashes.difference_update(phs)
        else:
            sub.puzzle_hashes.clear()
        return {"unsubscribed": phs or "all", "remaining": sorted(sub.puzzle_hashes)}

    raise ValueError(f"unknown subscribe method: {method}")


async def _dispatch(
    msg: dict, sub: Subscription, poller: Poller, rpc_fn: Callable[..., Any]
) -> dict | None:
    """Process one JSON-RPC request. Returns response dict, or None for invalid."""
    if not isinstance(msg, dict):
        return _err(None, -32600, "Invalid Request")
    id_ = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if not isinstance(method, str):
        return _err(id_, -32600, "Invalid Request: missing method")
    if not isinstance(params, dict):
        return _err(id_, -32602, "Invalid params (must be object)")

    try:
        if method in SUBSCRIBE_METHODS:
            result = await _handle_subscribe(method, params, sub, poller)
            return _ok(id_, result)
        if method in WS_RPC_METHODS:
            result = await asyncio.wait_for(
                asyncio.to_thread(rpc_fn, method, params), timeout=10.0
            )
            return _ok(id_, result)
        return _err(id_, -32601, f"Method not found or not allowed: {method}")
    except asyncio.TimeoutError:
        return _err(id_, -32000, "RPC timeout")
    except ValueError as e:
        return _err(id_, -32602, str(e))
    except Exception as e:
        return _err(id_, -32000, f"{type(e).__name__}: {e}")


def register_websocket(
    app: FastAPI,
    rpc_fn: Callable[..., Any],
    interval: float = 1.0,
) -> Poller:
    """Mount /ws endpoint and start the poller on app startup.

    Returns the Poller instance (caller can inspect state if needed).
    """
    registry = Registry()
    poller = Poller(rpc_fn, registry, interval=interval)

    # Compose with any existing lifespan (e.g. goby's FastAPI uses lifespan=...,
    # which makes on_event("startup") a no-op, so we wrap instead).
    _prev_lifespan = getattr(app.router, "lifespan_context", None)

    @contextlib.asynccontextmanager
    async def _combined_lifespan(scope):
        if _prev_lifespan is not None:
            async with _prev_lifespan(scope):
                await poller.start()
                try:
                    yield
                finally:
                    await poller.stop()
        else:
            await poller.start()
            try:
                yield
            finally:
                await poller.stop()

    app.router.lifespan_context = _combined_lifespan

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        sub = Subscription(ws)
        await registry.add(sub)
        log.info(f"ws connected (total={len(registry.snapshot())})")
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError as e:
                    await sub.send(_err(None, -32700, f"Parse error: {e}"))
                    continue
                # Support a single request or a batch
                if isinstance(msg, list):
                    responses = []
                    for m in msg:
                        r = await _dispatch(m, sub, poller, rpc_fn)
                        if r is not None and m.get("id") is not None:
                            responses.append(r)
                    if responses:
                        await sub.send(responses)
                    continue
                response = await _dispatch(msg, sub, poller, rpc_fn)
                # Don't respond to notifications (no id)
                if response is not None and msg.get("id") is not None:
                    await sub.send(response)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.warning(f"ws handler error: {type(e).__name__}: {e}")
        finally:
            await registry.remove(sub)
            log.info(f"ws disconnected (total={len(registry.snapshot())})")

    return poller
