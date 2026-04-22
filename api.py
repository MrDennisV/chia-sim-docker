"""
Combined API: Goby wallet backend + Simulator endpoints + Web UI
"""
import json, ssl, os, logging, time
import urllib.request
import yaml

# Set working directory for settings.toml and openapi module
os.chdir("/app")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Import Goby app and mount it
from openapi.api import app as goby_app, RPC_METHOD_WHITE_LIST
from network_config import NET

CHIA_ROOT = os.getenv("CHIA_ROOT", "/root/.chia/simulator/main")
RUNTIME_CONFIG = "/tmp/sim_runtime.json"

with open(f"{CHIA_ROOT}/config/config.yaml") as f:
    _cfg = yaml.safe_load(f)
RPC_PORT = _cfg["full_node"]["rpc_port"]
FARM_ADDR = _cfg.get("farmer", {}).get("xch_target_address", "")

RPC_URL = f"https://localhost:{RPC_PORT}"
CERT = f"{CHIA_ROOT}/config/ssl/full_node/private_full_node.crt"
KEY = f"{CHIA_ROOT}/config/ssl/full_node/private_full_node.key"

# Use Goby's app as the main app
app = goby_app

# API logger
_api_logger = logging.getLogger("sim_api")
_api_logger.setLevel(logging.INFO)
_api_handler = logging.FileHandler("/tmp/api.log")
_api_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_api_logger.addHandler(_api_handler)

_SKIP_LOG_PATHS = {"/healthz", "/logs/api", "/logs/node", "/", "/v1/chia_rpc"}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.time()
    response = await call_next(request)
    ms = round((time.time() - t0) * 1000)
    path = request.url.path
    if path not in _SKIP_LOG_PATHS:
        _api_logger.info(f"{request.method} {path} {response.status_code} {ms}ms")
    if response.status_code >= 400:
        _api_logger.warning(f"{request.method} {path} {response.status_code} {ms}ms")
    return response


# --- RPC helper for simulator-only endpoints ---

def rpc(path: str, body: dict | None = None) -> dict:
    if body is None:
        body = {}
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(CERT, KEY)
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{RPC_URL}/{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        resp = json.loads(r.read())
    if path == "get_network_info" and resp.get("network_name") == "simulator0":
        resp["network_name"] = NET["network_name"]
        resp["network_prefix"] = NET["network_prefix"]
    return resp


def _read_runtime():
    try:
        with open(RUNTIME_CONFIG) as f:
            return json.load(f)
    except Exception:
        return {"block_interval": 5}


def _write_runtime(cfg):
    with open(RUNTIME_CONFIG, "w") as f:
        json.dump(cfg, f)


# --- Simulator endpoints (not in Goby) ---

MOJO_PER_XCH = 1_000_000_000_000
REWARD_PER_BLOCK = 2 * MOJO_PER_XCH


@app.post("/fund_wallet")
async def fund_wallet(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON"}, 400)
    address = body.get("address")
    if not address:
        return JSONResponse({"success": False, "error": "address is required"}, 400)
    try:
        amount_xch = float(body.get("amount", 2.0))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "error": "amount must be a number"}, 400)
    if amount_xch <= 0:
        return JSONResponse({"success": False, "error": "amount must be positive"}, 400)
    target_mojos = int(amount_xch * MOJO_PER_XCH)
    blocks_needed = max(1, -(-target_mojos // REWARD_PER_BLOCK))
    farmed = []
    for _ in range(blocks_needed):
        result = rpc("farm_block", {"address": address, "guarantee_tx_block": True})
        farmed.append(result.get("new_peak_height"))
    return {
        "success": True,
        "address": address,
        "blocks_farmed": blocks_needed,
        "amount_xch": blocks_needed * 2.0,
        "amount_mojos": blocks_needed * REWARD_PER_BLOCK,
        "peak_heights": farmed,
    }


@app.post("/get_config")
async def get_config(request: Request):
    cfg = _read_runtime()
    cfg["auto_farm"] = cfg["block_interval"] == 0
    cfg["farm_address"] = FARM_ADDR
    cfg["rpc_port"] = RPC_PORT
    cfg["network"] = NET["network_name"]
    cfg["prefix"] = NET["network_prefix"]
    cfg["success"] = True
    return cfg


@app.post("/set_config")
async def set_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON"}, 400)
    cfg = _read_runtime()
    if "block_interval" in body:
        cfg["block_interval"] = max(0, int(body["block_interval"]))
        try:
            rpc("set_auto_farming", {"auto_farm": cfg["block_interval"] == 0})
        except Exception:
            pass
    _write_runtime(cfg)
    cfg["auto_farm"] = cfg["block_interval"] == 0
    cfg["success"] = True
    return cfg


# --- Logs ---

@app.get("/logs/node")
async def logs_node(lines: int = 50, level: str = ""):
    return _read_log(f"{CHIA_ROOT}/log/debug.log", lines, level)


@app.get("/logs/api")
async def logs_api(lines: int = 50, level: str = ""):
    return _read_log("/tmp/api.log", lines, level)


def _read_log(path: str, lines: int, level: str) -> dict:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, lines * 300)
            f.seek(max(0, size - chunk))
            raw = f.read().decode("utf-8", errors="replace").splitlines()
        tail = raw[-lines:]
        if level:
            level_upper = level.upper()
            tail = [l for l in tail if level_upper in l]
        return {"success": True, "lines": tail, "count": len(tail)}
    except FileNotFoundError:
        return {"success": True, "lines": [], "count": 0}
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Healthz ---

@app.get("/healthz")
async def health():
    try:
        d = rpc("get_blockchain_state")
        peak = d["blockchain_state"].get("peak")
        height = peak["height"] if peak else 0
        return {"success": True, "height": height, "synced": d["blockchain_state"]["sync"]["synced"]}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, 503)


# --- WebSocket service ---
# Mounts /ws with JSON-RPC 2.0 subscriptions (block peak, coins, puzzle hashes)
# and RPC passthrough (same whitelist chia-gaming-connect already uses).
from ws_endpoints import register_websocket, WS_RPC_METHODS

_ws_poll_interval = float(os.getenv("WS_POLL_INTERVAL_MS", "1000")) / 1000.0
register_websocket(app, rpc, interval=_ws_poll_interval)


# --- Simulator RPC passthrough (farm_block, revert_blocks, etc.) ---
# These are NOT in Goby's whitelist but useful for the simulator

SIM_ONLY_ENDPOINTS = {"farm_block", "set_auto_farming", "get_auto_farming",
                       "revert_blocks", "get_all_puzzle_hashes"}

for _ep in SIM_ONLY_ENDPOINTS:
    def _make_handler(ep_name):
        async def handler(request: Request):
            try:
                body = await request.json()
            except Exception:
                body = {}
            try:
                return JSONResponse(rpc(ep_name, body))
            except Exception as e:
                return JSONResponse({"success": False, "error": str(e)}, 500)
        return handler
    app.add_api_route(f"/{_ep}", _make_handler(_ep), methods=["POST"])


# --- Web UI ---

ENDPOINT_GROUPS = {
    "Full Node": [
        {"name": "get_blockchain_state", "body": "{}", "desc": "Current blockchain state"},
        {"name": "get_network_info", "body": "{}", "desc": "Network name and prefix"},
        {"name": "get_block_record_by_height", "body": '{"height": 1}', "desc": "Block record at height"},
        {"name": "get_block", "body": '{"header_hash": "0x..."}', "desc": "Full block by header hash"},
        {"name": "get_additions_and_removals", "body": '{"header_hash": "0x..."}', "desc": "Coins added/removed"},
        {"name": "get_fee_estimate", "body": '{"target_times": [60, 120, 300], "spend_type": "send_xch_transaction"}', "desc": "Fee estimates"},
    ],
    "Coins": [
        {"name": "get_coin_record_by_name", "body": '{"name": "0x..."}', "desc": "Coin by ID"},
        {"name": "get_coin_records_by_puzzle_hash", "body": '{"puzzle_hash": "0x...", "include_spent_coins": false}', "desc": "Coins by puzzle hash"},
        {"name": "get_coin_records_by_hint", "body": '{"hint": "0x...", "include_spent_coins": false}', "desc": "Coins by hint"},
        {"name": "get_puzzle_and_solution", "body": '{"coin_id": "0x...", "height": 1}', "desc": "Puzzle & solution"},
    ],
    "Mempool": [
        {"name": "get_mempool_item_by_tx_id", "body": '{"tx_id": "0x..."}', "desc": "Mempool item by tx ID"},
        {"name": "get_all_mempool_tx_ids", "body": "{}", "desc": "All mempool tx IDs"},
        {"name": "push_tx", "body": '{"spend_bundle": {}}', "desc": "Submit spend bundle"},
    ],
    "Goby v1": [
        {"name": "v1/chia_rpc", "body": '{"method": "get_blockchain_state", "params": {}}', "desc": "Goby RPC wrapper"},
        {"name": "v1/utxos", "body": '{"address": "' + NET["address_example"] + '"}', "desc": "UTXOs for address"},
        {"name": "v1/balance", "body": '{"address": "' + NET["address_example"] + '"}', "desc": "Balance for address"},
        {"name": "v1/sendtx", "body": '{"spend_bundle": {}}', "desc": "Send transaction"},
        {"name": "v1/fee_estimate", "body": '{"cost": 1000000}', "desc": "Fee estimate"},
        {"name": "v1/assets", "body": '{"address": "' + NET["address_example"] + '"}', "desc": "NFT/DID assets"},
    ],
    "Simulator": [
        {"name": "farm_block", "body": '{"address": "' + NET["address_example"] + '", "guarantee_tx_block": true}', "desc": "Farm a block"},
        {"name": "fund_wallet", "body": '{"address": "' + NET["address_example"] + '", "amount": 10.0}', "desc": "Fund wallet with XCH"},
        {"name": "revert_blocks", "body": '{"num_of_blocks": 1}', "desc": "Revert last N blocks"},
        {"name": "get_all_puzzle_hashes", "body": "{}", "desc": "All puzzle hashes with balances"},
    ],
    "Config": [
        {"name": "get_config", "body": "{}", "desc": "Get simulator config"},
        {"name": "set_config", "body": '{"block_interval": 5}', "desc": "Set block interval"},
        {"name": "logs/node", "body": '{"lines": 50, "level": ""}', "desc": "Simulator logs"},
        {"name": "logs/api", "body": '{"lines": 50, "level": ""}', "desc": "API request logs"},
    ],
}


# --- Web UI ---
# Dynamic config endpoint must be registered BEFORE mount so it wins routing.
@app.get("/web/config.js", response_class=Response)
async def ui_config():
    payload = {
        "groups": ENDPOINT_GROUPS,
        "ws_methods": sorted(WS_RPC_METHODS),
        "network": NET["network_name"],
        "prefix": NET["network_prefix"],
    }
    js = "window.__CONFIG__ = " + json.dumps(payload) + ";"
    return Response(content=js, media_type="application/javascript")


@app.get("/", response_class=HTMLResponse)
async def ui_index():
    return FileResponse("/app/web/index.html")


app.mount("/web", StaticFiles(directory="/app/web"), name="web")



# --- Coinset-compatible catch-all (MUST be last) ---
# Routes all POST /<rpc_method> directly to Chia RPC (no chain dependency)
from openapi.api import RPC_METHOD_WHITE_LIST as _WL


@app.api_route("/{rpc_method}", methods=["POST"])
async def coinset_compat_rpc(rpc_method: str, request: Request):
    if rpc_method not in _WL and rpc_method not in SIM_ONLY_ENDPOINTS:
        return JSONResponse({"success": False, "error": f"unsupported rpc method: {rpc_method}"}, 400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return JSONResponse(rpc(rpc_method, body))
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, 500)
