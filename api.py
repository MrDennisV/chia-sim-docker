import json, ssl, os
import urllib.request
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

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
        return json.loads(r.read())


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
        {"name": "get_additions_and_removals", "body": '{"header_hash": "0x..."}', "desc": "Coins added/removed in block"},
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
        {"name": "push_tx", "body": '{"spend_bundle": {}}', "desc": "Submit spend bundle"},
    ],
    "Simulator": [
        {"name": "farm_block", "body": '{"address": "txch1...", "guarantee_tx_block": true}', "desc": "Farm a block"},
        {"name": "fund_wallet", "body": '{"address": "txch1...", "amount": 10.0}', "desc": "Fund wallet with XCH"},
        {"name": "set_auto_farming", "body": '{"auto_farm": true}', "desc": "Toggle auto-farming (use set_config instead)"},
        {"name": "get_auto_farming", "body": "{}", "desc": "Auto-farming status"},
        {"name": "revert_blocks", "body": '{"num_of_blocks": 1}', "desc": "Revert last N blocks"},
        {"name": "get_all_puzzle_hashes", "body": "{}", "desc": "All puzzle hashes with balances"},
    ],
    "Config": [
        {"name": "get_config", "body": "{}", "desc": "Get simulator config"},
        {"name": "set_config", "body": '{"block_interval": 5}', "desc": "Set block interval (0=auto-farm on tx, >0=periodic seconds)"},
        {"name": "logs", "body": '{"lines": 50, "level": ""}', "desc": "View logs (level: INFO, WARNING, ERROR)"},
    ],
}

for group in ENDPOINT_GROUPS.values():
    for ep in group:
        n = ep["name"]
        if n not in ("fund_wallet", "get_config", "set_config"):
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
    cfg["network"] = "simulator0"
    cfg["prefix"] = "txch"
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


@app.get("/logs")
async def logs(lines: int = 50, level: str = ""):
    log_path = f"{CHIA_ROOT}/log/debug.log"
    try:
        with open(log_path, "rb") as f:
            # Read from end of file efficiently
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
        return JSONResponse({"success": False, "error": "Log file not found"}, 404)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, 500)


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
const getEps=['logs'];

function buildNav(){{
  const nav=document.getElementById('nav');
  let html='';
  for(const[group,eps]of Object.entries(G)){{
    html+='<div class="group-title">'+group+'</div>';
    for(const ep of eps){{
      const isSim=simEps.includes(ep.name),isCfg=cfgEps.includes(ep.name);
      const isGet=getEps.includes(ep.name);
      const bc=isCfg?'badge-cfg':isSim?'badge-sim':'badge-post';
      const bl=isGet?'GET':isCfg?'CFG':isSim?'SIM':'POST';
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
