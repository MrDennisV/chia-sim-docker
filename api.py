"""
Combined API: Goby wallet backend + Simulator endpoints + Web UI
"""
import json, ssl, os, logging, time
import urllib.request
import yaml

# Set working directory for settings.toml and openapi module
os.chdir("/app")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
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
from ws_endpoints import register_websocket

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
.layout{{display:flex;height:100vh}}
.sidebar{{width:260px;min-width:260px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}}
.sidebar-head{{padding:14px 16px 10px;border-bottom:1px solid var(--border)}}
.sidebar-head h1{{font-size:13px;color:var(--accent);font-weight:600}}
.sidebar-head h1 span{{color:var(--muted);font-weight:400}}
.nav-scroll{{flex:1;overflow-y:auto;padding:4px 0}}
.group-title{{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);padding:12px 14px 4px;font-weight:700}}
.ep-btn{{display:block;width:100%;text-align:left;background:none;border:none;color:var(--text);padding:7px 14px;cursor:pointer;font-family:var(--font);font-size:11px;transition:background .1s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ep-btn:hover{{background:var(--border)}}
.ep-btn.active{{background:var(--border);color:var(--accent)}}
.badge{{display:inline-block;font-size:8px;padding:1px 4px;border-radius:3px;margin-right:5px;font-weight:700;vertical-align:middle}}
.badge-post{{background:#22c55e18;color:var(--accent)}}
.badge-sim{{background:#f59e0b18;color:var(--sim)}}
.badge-cfg{{background:#a78bfa18;color:var(--cfg)}}
.badge-goby{{background:#3b82f618;color:var(--accent2)}}
.health{{padding:8px 14px;border-top:1px solid var(--border);font-size:10px;color:var(--muted);display:flex;align-items:center;gap:6px}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--accent);flex-shrink:0}}
.dot.off{{background:var(--red)}}
.main{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
.topbar{{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;background:var(--surface);flex-wrap:wrap;min-height:42px}}
.topbar .method{{color:var(--accent);font-weight:700;font-size:11px}}
.topbar .path{{color:var(--text);font-size:12px}}
.topbar .desc{{color:var(--muted);font-size:10px;margin-left:auto}}
.content{{flex:1;display:flex;overflow:hidden}}
.editor-pane,.result-pane{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
.editor-pane{{border-right:1px solid var(--border)}}
.pane-header{{padding:6px 12px;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);border-bottom:1px solid var(--border);background:var(--surface);display:flex;align-items:center;justify-content:space-between;gap:6px;flex-shrink:0}}
textarea{{flex:1;background:var(--bg);color:var(--text);border:none;padding:10px 12px;font-family:var(--font);font-size:12px;resize:none;outline:none;width:100%;min-height:60px}}
pre{{flex:1;background:var(--bg);color:var(--text);padding:10px 12px;overflow:auto;font-family:var(--font);font-size:11px;white-space:pre-wrap;word-break:break-all}}
.send-btn{{background:var(--accent);color:#000;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;font-family:var(--font);font-size:11px;font-weight:700}}
.send-btn:hover{{opacity:.85}}
.send-btn:disabled{{opacity:.5;cursor:wait}}
.status{{font-size:10px;padding:0 6px}}
.status.ok{{color:var(--accent)}}
.status.err{{color:var(--red)}}
.menu-toggle{{display:none;background:none;border:none;color:var(--text);font-size:20px;cursor:pointer;padding:10px 14px;position:fixed;top:0;left:0;z-index:101}}
.overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99}}
.overlay.open{{display:block}}
@media(max-width:768px){{
  .menu-toggle{{display:block}}
  .sidebar{{position:fixed;top:0;left:-100%;width:80vw;min-width:auto;height:100vh;z-index:100;transition:left .2s ease;padding-top:44px}}
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
    <div class="sidebar-head"><h1>Chia Simulator <span>+ Goby</span></h1></div>
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
const simEps=['farm_block','revert_blocks','get_all_puzzle_hashes','fund_wallet'];
const cfgEps=['get_config','set_config'];
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
function toggleMenu(){{document.getElementById('sidebar').classList.toggle('open');document.getElementById('overlay').classList.toggle('open')}}
function sel(btn){{
  document.querySelectorAll('.ep-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');cur=btn.dataset.ep;
  document.getElementById('epPath').textContent='/'+cur;
  document.getElementById('epDesc').textContent=btn.dataset.desc;
  document.querySelector('.topbar .method').textContent=getEps.includes(cur)?'GET':'POST';
  try{{document.getElementById('reqBody').value=JSON.stringify(JSON.parse(btn.dataset.body),null,2)}}
  catch{{document.getElementById('reqBody').value=btn.dataset.body}}
  document.getElementById('resBody').textContent='';document.getElementById('status').textContent='';document.getElementById('timing').textContent='';
  if(window.innerWidth<=768)toggleMenu();
}}
async function send(){{
  const btn=document.getElementById('sendBtn'),st=document.getElementById('status'),tm=document.getElementById('timing');
  btn.disabled=true;st.textContent='';const t0=performance.now();
  try{{
    const body=document.getElementById('reqBody').value.trim();let r;
    if(getEps.includes(cur)){{let qs='';try{{const p=JSON.parse(body||'{{}}');qs='?'+new URLSearchParams(p).toString()}}catch{{}}r=await fetch('/'+cur+qs)}}
    else{{r=await fetch('/'+cur,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:body||'{{}}'}})}}
    const d=await r.json();const ms=Math.round(performance.now()-t0);
    tm.textContent=ms+'ms';document.getElementById('resBody').textContent=JSON.stringify(d,null,2);
    st.className='status '+(d.success!==false?'ok':'err');st.textContent=d.success!==false?'OK':'ERR';
  }}catch(e){{document.getElementById('resBody').textContent=e.toString();st.className='status err';st.textContent='ERR';tm.textContent=Math.round(performance.now()-t0)+'ms'}}
  btn.disabled=false;
}}
async function checkHealth(){{
  try{{const r=await fetch('/healthz');const d=await r.json();document.getElementById('healthDot').className='dot'+(d.success?'':' off');document.getElementById('healthText').textContent=d.success?'Height: '+d.height:'offline'}}
  catch{{document.getElementById('healthDot').className='dot off';document.getElementById('healthText').textContent='offline'}}
}}
document.getElementById('reqBody').addEventListener('keydown',e=>{{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')send()}});
buildNav();checkHealth();setInterval(checkHealth,5000);
</script>
</body>
</html>"""


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
