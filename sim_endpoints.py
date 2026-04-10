import json, ssl, os, logging, time
import urllib.request
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI(title="Chia Simulator API", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# API logger
_api_logger = logging.getLogger("api")
_api_logger.setLevel(logging.INFO)
_api_handler = logging.FileHandler("/tmp/api.log")
_api_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_api_logger.addHandler(_api_handler)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.time()
    response = await call_next(request)
    ms = round((time.time() - t0) * 1000)
    if request.url.path not in ("/healthz", "/logs/api", "/logs/node", "/"):
        _api_logger.info(f"{request.method} {request.url.path} {response.status_code} {ms}ms")
    if response.status_code >= 400:
        _api_logger.warning(f"{request.method} {request.url.path} {response.status_code} {ms}ms")
    return response


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


def route(path: str):
    async def handler(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            return JSONResponse(rpc(path, body))
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    return handler


ENDPOINT_GROUPS = {
    "Full Node": [
        {"name": "get_blockchain_state", "body": "{}", "desc": "Current blockchain state"},
        {"name": "get_network_info", "body": "{}", "desc": "Network name and prefix"},
        {"name": "get_block_record_by_height", "body": '{"height": 1}', "desc": "Block record at height"},
        {"name": "get_block_record", "body": '{"header_hash": "0x..."}', "desc": "Block record by header hash"},
        {"name": "get_block_records", "body": '{"start": 0, "end": 5}', "desc": "Block records in range"},
        {"name": "get_block", "body": '{"header_hash": "0x..."}', "desc": "Full block by header hash"},
        {"name": "get_blocks", "body": '{"start": 0, "end": 5}', "desc": "Full blocks in range"},
        {"name": "get_block_spends", "body": '{"header_hash": "0x..."}', "desc": "Spends in a block"},
        {"name": "get_additions_and_removals", "body": '{"header_hash": "0x..."}', "desc": "Coins added/removed in block"},
        {"name": "get_routes", "body": "{}", "desc": "List all available RPC routes"},
        {"name": "get_fee_estimate", "body": '{"target_times": [60, 120, 300], "spend_type": "send_xch_transaction"}', "desc": "Fee estimates"},
    ],
    "Coins": [
        {"name": "get_coin_record_by_name", "body": '{"name": "0x..."}', "desc": "Coin by ID"},
        {"name": "get_coin_records_by_names", "body": '{"names": ["0x..."], "include_spent_coins": false}', "desc": "Coins by IDs"},
        {"name": "get_coin_records_by_puzzle_hash", "body": '{"puzzle_hash": "0x...", "include_spent_coins": false}', "desc": "Coins by puzzle hash"},
        {"name": "get_coin_records_by_puzzle_hashes", "body": '{"puzzle_hashes": ["0x..."], "include_spent_coins": false}', "desc": "Coins by puzzle hashes"},
        {"name": "get_coin_records_by_parent_ids", "body": '{"parent_ids": ["0x..."], "include_spent_coins": false}', "desc": "Coins by parent IDs"},
        {"name": "get_coin_records_by_hint", "body": '{"hint": "0x...", "include_spent_coins": false}', "desc": "Coins by hint"},
        {"name": "get_puzzle_and_solution", "body": '{"coin_id": "0x...", "height": 1}', "desc": "Puzzle & solution for spent coin"},
    ],
    "Mempool": [
        {"name": "get_mempool_item_by_tx_id", "body": '{"tx_id": "0x..."}', "desc": "Mempool item by tx ID"},
        {"name": "get_mempool_items_by_coin_name", "body": '{"coin_name": "0x..."}', "desc": "Mempool items by coin"},
        {"name": "get_all_mempool_tx_ids", "body": "{}", "desc": "All mempool transaction IDs"},
        {"name": "get_all_mempool_items", "body": "{}", "desc": "All mempool items"},
        {"name": "push_tx", "body": '{"spend_bundle": {}}', "desc": "Submit spend bundle"},
    ],
    "Simulator": [
        {"name": "farm_block", "body": '{"address": "' + NET["address_example"] + '", "guarantee_tx_block": true}', "desc": "Farm a block"},
        {"name": "fund_wallet", "body": '{"address": "' + NET["address_example"] + '", "amount": 10.0}', "desc": "Fund wallet with XCH"},
        {"name": "set_auto_farming", "body": '{"auto_farm": true}', "desc": "Toggle auto-farming (use set_config instead)"},
        {"name": "get_auto_farming", "body": "{}", "desc": "Auto-farming status"},
        {"name": "revert_blocks", "body": '{"num_of_blocks": 1}', "desc": "Revert last N blocks"},
        {"name": "get_all_puzzle_hashes", "body": "{}", "desc": "All puzzle hashes with balances"},
    ],
    "Goby v1": [
        {"name": "v1/chia_rpc", "body": '{"method": "get_blockchain_state", "params": {}}', "desc": "Goby RPC wrapper"},
        {"name": "v1/utxos", "body": '{"address": "' + NET["address_example"] + '"}', "desc": "UTXOs for address"},
        {"name": "v1/balance", "body": '{"address": "' + NET["address_example"] + '"}', "desc": "Balance for address"},
        {"name": "v1/sendtx", "body": '{"spend_bundle": {}}', "desc": "Send transaction"},
        {"name": "v1/fee_estimate", "body": '{"cost": 1000000}', "desc": "Fee estimate"},
        {"name": "v1/assets", "body": '{"address": "' + NET["address_example"] + '"}', "desc": "NFT/DID assets"},
    ],
    "Config": [
        {"name": "get_config", "body": "{}", "desc": "Get simulator config"},
        {"name": "set_config", "body": '{"block_interval": 5}', "desc": "Set block interval (0=auto-farm on tx, >0=periodic seconds)"},
        {"name": "logs/node", "body": '{"lines": 50, "level": ""}', "desc": "Simulator full node logs"},
        {"name": "logs/api", "body": '{"lines": 50, "level": ""}', "desc": "API request logs"},
    ],
}

_SKIP_AUTO_ROUTE = {"fund_wallet", "get_config", "set_config", "logs/node", "logs/api",
                     "v1/chia_rpc", "v1/utxos", "v1/balance", "v1/sendtx",
                     "v1/fee_estimate", "v1/assets"}
for group in ENDPOINT_GROUPS.values():
    for ep in group:
        n = ep["name"]
        if n not in _SKIP_AUTO_ROUTE:
            app.add_api_route(f"/{n}", route(n), methods=["POST"])

# --- fund_wallet ---

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


# --- config ---


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
        # interval=0 means auto-farm on push_tx, >0 means periodic
        try:
            rpc("set_auto_farming", {"auto_farm": cfg["block_interval"] == 0})
        except Exception:
            pass
    _write_runtime(cfg)
    cfg["auto_farm"] = cfg["block_interval"] == 0
    cfg["success"] = True
    return cfg


# --- logs ---

API_LOG = "/tmp/api.log"


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


@app.get("/logs/node")
async def logs_node(lines: int = 50, level: str = ""):
    return _read_log(f"{CHIA_ROOT}/log/debug.log", lines, level)


@app.get("/logs/api")
async def logs_api(lines: int = 50, level: str = ""):
    return _read_log(API_LOG, lines, level)


# --- Goby /v1/ endpoints ---

NETWORK_PREFIX = NET["network_prefix"]

RPC_WHITE_LIST = {
    "get_puzzle_and_solution", "get_coin_records_by_puzzle_hash",
    "get_coin_records_by_puzzle_hashes", "get_coin_record_by_name",
    "get_coin_records_by_names", "get_coin_records_by_parent_ids",
    "get_blockchain_state", "get_block_record_by_height", "get_network_info",
    "get_all_mempool_tx_ids", "get_mempool_item_by_tx_id",
    "get_coin_records_by_hint", "get_additions_and_removals",
    "push_tx", "get_fee_estimate", "get_mempool_items_by_coin_name",
    "get_all_mempool_items", "get_block", "get_blocks", "get_block_record",
    "get_block_records", "get_block_spends", "get_routes",
}


def _bech32_to_puzzle_hash(address: str) -> str:
    """Decode txch/xch bech32m address to 0x-prefixed puzzle hash hex."""
    # Inline bech32m decode to avoid importing chia modules
    CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    _, data_part = address.rsplit("1", 1)
    values = [CHARSET.find(c) for c in data_part]
    # Convert 5-bit groups to 8-bit
    acc, bits, result = 0, 0, []
    for v in values[:-6]:  # strip checksum
        acc = (acc << 5) | v
        bits += 5
        while bits >= 8:
            bits -= 8
            result.append((acc >> bits) & 0xFF)
    return "0x" + bytes(result).hex()


@app.post("/v1/chia_rpc")
async def goby_chia_rpc(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, 400)
    method = body.get("method")
    params = body.get("params") or {}
    if method not in RPC_WHITE_LIST:
        return JSONResponse({"detail": f"unsupported rpc method: {method}"}, 400)
    try:
        return JSONResponse(rpc(method, params))
    except Exception as e:
        return JSONResponse({"detail": str(e)}, 500)


@app.get("/v1/utxos")
async def goby_utxos(address: str):
    try:
        ph = _bech32_to_puzzle_hash(address)
    except Exception:
        return JSONResponse({"detail": "Invalid Address"}, 400)
    try:
        resp = rpc("get_coin_records_by_puzzle_hash", {
            "puzzle_hash": ph, "include_spent_coins": False
        })
        data = []
        for row in resp.get("coin_records", []):
            if row.get("spent"):
                continue
            coin = row["coin"]
            data.append({
                "parent_coin_info": coin["parent_coin_info"],
                "puzzle_hash": coin["puzzle_hash"],
                "amount": str(coin["amount"]),
            })
        return data
    except Exception as e:
        return JSONResponse({"detail": str(e)}, 500)


@app.get("/v1/balance")
async def goby_balance(address: str):
    try:
        ph = _bech32_to_puzzle_hash(address)
    except Exception:
        return JSONResponse({"detail": "Invalid Address"}, 400)
    try:
        resp = rpc("get_coin_records_by_puzzle_hash", {
            "puzzle_hash": ph, "include_spent_coins": False
        })
        amount = 0
        coin_num = 0
        for row in resp.get("coin_records", []):
            if row.get("spent"):
                continue
            amount += row["coin"]["amount"]
            coin_num += 1
        return {"amount": amount, "coin_num": coin_num}
    except Exception as e:
        return JSONResponse({"detail": str(e)}, 500)


@app.post("/v1/sendtx")
async def goby_sendtx(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, 400)
    spend_bundle = body.get("spend_bundle")
    if not spend_bundle:
        return JSONResponse({"detail": "spend_bundle required"}, 400)
    try:
        resp = rpc("push_tx", {"spend_bundle": spend_bundle})
        return {"status": resp.get("status", 1)}
    except Exception as e:
        return JSONResponse({"detail": str(e)}, 400)


@app.post("/v1/fee_estimate")
async def goby_fee_estimate(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, 400)
    cost = body.get("cost", 0)
    if cost <= 0:
        return JSONResponse({"detail": "invalid cost"}, 400)
    try:
        resp = rpc("get_fee_estimate", {
            "target_times": [30, 120, 300],
            "cost": cost,
            "spend_type": "send_xch_transaction",
        })
        estimates = resp.get("estimates", [0, 0, 0])
        mempool_size = resp.get("mempool_size", 0)
        mempool_max = resp.get("mempool_max_size", 0)
        is_full = cost + mempool_size > mempool_max if mempool_max > 0 else False
        if is_full:
            min_fee = 5 * cost
            estimates = [int(min_fee * 1.5), int(min_fee * 1.1), min_fee]
        return {"estimates": estimates}
    except Exception as e:
        return JSONResponse({"detail": str(e)}, 500)


@app.get("/v1/assets")
async def goby_assets(address: str, asset_type: str = "nft", asset_id: str = None, offset: int = 0, limit: int = 10):
    # Simulator doesn't have NFT/DID indexer — return empty list
    return []


@app.get("/v1/latest_singleton")
async def goby_latest_singleton(singleton_id: str):
    return JSONResponse({"detail": "not found"}, 404)


# --- healthz ---


@app.get("/healthz")
async def health():
    try:
        d = rpc("get_blockchain_state")
        peak = d["blockchain_state"].get("peak")
        height = peak["height"] if peak else 0
        return {"success": True, "height": height, "synced": d["blockchain_state"]["sync"]["synced"]}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, 503)


# --- Web UI ---

@app.get("/", response_class=HTMLResponse)
async def ui():
    groups_json = json.dumps(ENDPOINT_GROUPS)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chia Simulator API</title>
<style>
:root {{
  --bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--text:#e1e4ed;
  --muted:#8b8fa3;--accent:#22c55e;--accent2:#3b82f6;--red:#ef4444;
  --sim:#f59e0b;--cfg:#a78bfa;
  --font:'SF Mono','Cascadia Code','Fira Code','Courier New',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px}}

/* Layout */
.layout{{display:flex;height:100vh}}
.sidebar{{
  width:260px;min-width:260px;background:var(--surface);border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden;
}}
.sidebar-head{{padding:14px 16px 10px;border-bottom:1px solid var(--border)}}
.sidebar-head h1{{font-size:13px;color:var(--accent);font-weight:600}}
.sidebar-head h1 span{{color:var(--muted);font-weight:400}}
.nav-scroll{{flex:1;overflow-y:auto;padding:4px 0}}
.group-title{{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);padding:12px 14px 4px;font-weight:700}}
.ep-btn{{
  display:block;width:100%;text-align:left;background:none;border:none;
  color:var(--text);padding:7px 14px;cursor:pointer;font-family:var(--font);
  font-size:11px;transition:background .1s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.ep-btn:hover{{background:var(--border)}}
.ep-btn.active{{background:var(--border);color:var(--accent)}}
.badge{{display:inline-block;font-size:8px;padding:1px 4px;border-radius:3px;margin-right:5px;font-weight:700;vertical-align:middle}}
.badge-post{{background:#22c55e18;color:var(--accent)}}
.badge-sim{{background:#f59e0b18;color:var(--sim)}}
.badge-cfg{{background:#a78bfa18;color:var(--cfg)}}
.badge-goby{{background:#3b82f618;color:var(--accent2)}}
.health{{
  padding:8px 14px;border-top:1px solid var(--border);font-size:10px;
  color:var(--muted);display:flex;align-items:center;gap:6px;
}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--accent);flex-shrink:0}}
.dot.off{{background:var(--red)}}

/* Main */
.main{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
.topbar{{
  padding:10px 16px;border-bottom:1px solid var(--border);display:flex;
  align-items:center;gap:8px;background:var(--surface);flex-wrap:wrap;min-height:42px;
}}
.topbar .method{{color:var(--accent);font-weight:700;font-size:11px}}
.topbar .path{{color:var(--text);font-size:12px}}
.topbar .desc{{color:var(--muted);font-size:10px;margin-left:auto}}
.content{{flex:1;display:flex;overflow:hidden}}
.editor-pane,.result-pane{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
.editor-pane{{border-right:1px solid var(--border)}}
.pane-header{{
  padding:6px 12px;font-size:9px;text-transform:uppercase;letter-spacing:1px;
  color:var(--muted);border-bottom:1px solid var(--border);background:var(--surface);
  display:flex;align-items:center;justify-content:space-between;gap:6px;flex-shrink:0;
}}
textarea{{
  flex:1;background:var(--bg);color:var(--text);border:none;padding:10px 12px;
  font-family:var(--font);font-size:12px;resize:none;outline:none;width:100%;min-height:60px;
}}
pre{{
  flex:1;background:var(--bg);color:var(--text);padding:10px 12px;overflow:auto;
  font-family:var(--font);font-size:11px;white-space:pre-wrap;word-break:break-all;
}}
.send-btn{{
  background:var(--accent);color:#000;border:none;padding:6px 16px;border-radius:4px;
  cursor:pointer;font-family:var(--font);font-size:11px;font-weight:700;
}}
.send-btn:hover{{opacity:.85}}
.send-btn:disabled{{opacity:.5;cursor:wait}}
.status{{font-size:10px;padding:0 6px}}
.status.ok{{color:var(--accent)}}
.status.err{{color:var(--red)}}

/* Mobile hamburger */
.menu-toggle{{
  display:none;background:none;border:none;color:var(--text);font-size:20px;
  cursor:pointer;padding:10px 14px;position:fixed;top:0;left:0;z-index:101;
}}
.overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99}}
.overlay.open{{display:block}}

/* Responsive */
@media(max-width:768px){{
  .menu-toggle{{display:block}}
  .sidebar{{
    position:fixed;top:0;left:-100%;width:80vw;min-width:auto;height:100vh;
    z-index:100;transition:left .2s ease;padding-top:44px;
  }}
  .sidebar.open{{left:0}}
  .topbar{{padding:10px 10px 10px 44px}}
  .topbar .desc{{display:none}}
  .content{{flex-direction:column}}
  .editor-pane{{border-right:none;border-bottom:1px solid var(--border);max-height:35vh;min-height:100px}}
  .result-pane{{min-height:0}}
  .ep-btn{{padding:10px 14px;font-size:12px}}
  .group-title{{font-size:10px}}
  textarea{{font-size:13px}}
  pre{{font-size:11px}}
}}
</style>
</head>
<body>
<div class="layout">
  <button class="menu-toggle" id="menuBtn" onclick="toggleMenu()">&#9776;</button>
  <div class="overlay" id="overlay" onclick="toggleMenu()"></div>
  <div class="sidebar" id="sidebar">
    <div class="sidebar-head"><h1>Chia Simulator <span>API</span></h1></div>
    <div class="nav-scroll" id="nav"></div>
    <div class="health"><div class="dot" id="healthDot"></div><span id="healthText">connecting...</span></div>
  </div>
  <div class="main">
    <div class="topbar">
      <span class="method">POST</span>
      <span class="path" id="epPath">/get_blockchain_state</span>
      <span class="desc" id="epDesc">Current blockchain state</span>
    </div>
    <div class="content">
      <div class="editor-pane">
        <div class="pane-header">
          <span>Request Body</span>
          <div style="display:flex;align-items:center;gap:6px">
            <button class="send-btn" id="sendBtn" onclick="send()">Send</button>
            <span class="status" id="status"></span>
          </div>
        </div>
        <textarea id="reqBody">{{}}</textarea>
      </div>
      <div class="result-pane">
        <div class="pane-header"><span>Response</span><span id="timing"></span></div>
        <pre id="resBody">Select an endpoint and press Send (or Ctrl+Enter)</pre>
      </div>
    </div>
  </div>
</div>
<script>
const G={groups_json};
let cur='get_blockchain_state';
const simEps=['farm_block','set_auto_farming','get_auto_farming','revert_blocks','get_all_puzzle_hashes','fund_wallet'];
const cfgEps=['get_config','set_config','logs'];
const gobyEps=['v1/chia_rpc','v1/utxos','v1/balance','v1/sendtx','v1/fee_estimate','v1/assets'];
const getEps=['logs/node','logs/api','v1/utxos','v1/balance','v1/assets'];

function buildNav(){{
  const nav=document.getElementById('nav');
  let html='';
  for(const[group,eps]of Object.entries(G)){{
    html+='<div class="group-title">'+group+'</div>';
    for(const ep of eps){{
      const isSim=simEps.includes(ep.name),isCfg=cfgEps.includes(ep.name),isGoby=gobyEps.includes(ep.name);
      const isGet=getEps.includes(ep.name);
      const bc=isGoby?'badge-goby':isCfg?'badge-cfg':isSim?'badge-sim':'badge-post';
      const bl=isGet?'GET':isGoby?'GOBY':isCfg?'CFG':isSim?'SIM':'POST';
      const esc=ep.body.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
      html+='<button class="ep-btn'+(ep.name===cur?' active':'')+'" data-ep="'+ep.name+'" data-body="'+esc+'" data-desc="'+ep.desc+'" onclick="sel(this)"><span class="badge '+bc+'">'+bl+'</span>'+ep.name+'</button>';
    }}
  }}
  nav.innerHTML=html;
}}

function toggleMenu(){{
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('overlay').classList.toggle('open');
}}

function sel(btn){{
  document.querySelectorAll('.ep-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  cur=btn.dataset.ep;
  document.getElementById('epPath').textContent='/'+cur;
  document.getElementById('epDesc').textContent=btn.dataset.desc;
  document.querySelector('.topbar .method').textContent=getEps.includes(cur)?'GET':'POST';
  try{{document.getElementById('reqBody').value=JSON.stringify(JSON.parse(btn.dataset.body),null,2)}}
  catch{{document.getElementById('reqBody').value=btn.dataset.body}}
  document.getElementById('resBody').textContent='';
  document.getElementById('status').textContent='';
  document.getElementById('timing').textContent='';
  if(window.innerWidth<=768)toggleMenu();
}}

async function send(){{
  const btn=document.getElementById('sendBtn'),st=document.getElementById('status'),tm=document.getElementById('timing');
  btn.disabled=true;st.textContent='';
  const t0=performance.now();
  try{{
    const body=document.getElementById('reqBody').value.trim();
    let r;
    if(getEps.includes(cur)){{
      let qs='';
      try{{const p=JSON.parse(body||'{{}}');qs='?'+new URLSearchParams(p).toString()}}catch{{}}
      r=await fetch('/'+cur+qs);
    }}else{{
      r=await fetch('/'+cur,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:body||'{{}}'}});
    }}
    const d=await r.json();
    const ms=Math.round(performance.now()-t0);
    tm.textContent=ms+'ms';
    document.getElementById('resBody').textContent=JSON.stringify(d,null,2);
    st.className='status '+(d.success?'ok':'err');
    st.textContent=d.success?'OK':'ERR';
  }}catch(e){{
    document.getElementById('resBody').textContent=e.toString();
    st.className='status err';st.textContent='ERR';
    tm.textContent=Math.round(performance.now()-t0)+'ms';
  }}
  btn.disabled=false;
}}

async function checkHealth(){{
  try{{
    const r=await fetch('/healthz');const d=await r.json();
    document.getElementById('healthDot').className='dot'+(d.success?'':' off');
    document.getElementById('healthText').textContent=d.success?'Height: '+d.height:'offline';
  }}catch{{
    document.getElementById('healthDot').className='dot off';
    document.getElementById('healthText').textContent='offline';
  }}
}}

document.getElementById('reqBody').addEventListener('keydown',e=>{{
  if((e.metaKey||e.ctrlKey)&&e.key==='Enter')send();
}});

buildNav();checkHealth();setInterval(checkHealth,5000);
</script>
</body>
</html>"""
