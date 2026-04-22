// REST + WebSocket playground logic.
// Reads window.__CONFIG__ (injected by /web/config.js).

const G = window.__CONFIG__.groups;
const WS_METHODS = window.__CONFIG__.ws_methods;

const simEps  = ['farm_block', 'revert_blocks', 'get_all_puzzle_hashes', 'fund_wallet'];
const cfgEps  = ['get_config', 'set_config'];
const gobyEps = ['v1/chia_rpc', 'v1/utxos', 'v1/balance', 'v1/sendtx', 'v1/fee_estimate', 'v1/assets'];
const getEps  = ['logs/node', 'logs/api', 'v1/utxos', 'v1/balance', 'v1/assets'];

let curEp = 'get_blockchain_state';

// ── tabs ──────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.getElementById('tab-rest').classList.toggle('hidden', name !== 'rest');
  document.getElementById('tab-ws').classList.toggle('hidden', name !== 'ws');
}
function toggleMenu(panelId, overlayId) {
  document.getElementById(panelId).classList.toggle('open');
  document.getElementById(overlayId).classList.toggle('open');
}

// ── REST nav + send ───────────────────────────────────
function buildNav() {
  const nav = document.getElementById('nav');
  let html = '';
  for (const [group, eps] of Object.entries(G)) {
    html += '<div class="group-title">' + group + '</div>';
    for (const ep of eps) {
      const isSim = simEps.includes(ep.name);
      const isCfg = cfgEps.includes(ep.name);
      const isGoby = gobyEps.includes(ep.name);
      const isGet = getEps.includes(ep.name);
      const bc = isGoby ? 'badge-goby' : isCfg ? 'badge-cfg' : isSim ? 'badge-sim' : 'badge-post';
      const bl = isGet ? 'GET' : isGoby ? 'GOBY' : isCfg ? 'CFG' : isSim ? 'SIM' : 'POST';
      const esc = ep.body
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
      html += '<button class="ep-btn' + (ep.name === curEp ? ' active' : '') +
              '" data-ep="' + ep.name + '" data-body="' + esc +
              '" data-desc="' + ep.desc + '" onclick="selEp(this)">' +
              '<span class="badge ' + bc + '">' + bl + '</span>' + ep.name + '</button>';
    }
  }
  nav.innerHTML = html;
}

function selEp(btn) {
  document.querySelectorAll('.ep-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  curEp = btn.dataset.ep;
  document.getElementById('epPath').textContent = '/' + curEp;
  document.getElementById('epDesc').textContent = btn.dataset.desc;
  document.querySelector('.topbar .method').textContent = getEps.includes(curEp) ? 'GET' : 'POST';
  try {
    document.getElementById('reqBody').value = JSON.stringify(JSON.parse(btn.dataset.body), null, 2);
  } catch {
    document.getElementById('reqBody').value = btn.dataset.body;
  }
  document.getElementById('resBody').textContent = '';
  document.getElementById('status').textContent = '';
  document.getElementById('timing').textContent = '';
  if (window.innerWidth <= 768) toggleMenu('sidebar', 'overlayRest');
}

async function restSend() {
  const btn = document.getElementById('sendBtn');
  const st = document.getElementById('status');
  const tm = document.getElementById('timing');
  btn.disabled = true;
  st.textContent = '';
  const t0 = performance.now();
  try {
    const body = document.getElementById('reqBody').value.trim();
    let r;
    if (getEps.includes(curEp)) {
      let qs = '';
      try {
        const p = JSON.parse(body || '{}');
        qs = '?' + new URLSearchParams(p).toString();
      } catch {}
      r = await fetch('/' + curEp + qs);
    } else {
      r = await fetch('/' + curEp, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body || '{}',
      });
    }
    const d = await r.json();
    const ms = Math.round(performance.now() - t0);
    tm.textContent = ms + 'ms';
    document.getElementById('resBody').textContent = JSON.stringify(d, null, 2);
    st.className = 'status ' + (d.success !== false ? 'ok' : 'err');
    st.textContent = d.success !== false ? 'OK' : 'ERR';
  } catch (e) {
    document.getElementById('resBody').textContent = e.toString();
    st.className = 'status err';
    st.textContent = 'ERR';
    tm.textContent = Math.round(performance.now() - t0) + 'ms';
  }
  btn.disabled = false;
}

// ── Health ────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch('/healthz');
    const d = await r.json();
    document.getElementById('healthDot').className = 'dot' + (d.success ? '' : ' off');
    document.getElementById('healthText').textContent = d.success ? ('Height: ' + d.height) : 'offline';
  } catch {
    document.getElementById('healthDot').className = 'dot off';
    document.getElementById('healthText').textContent = 'offline';
  }
}

// ── WebSocket playground ──────────────────────────────
let ws = null;
let wsReqId = 0;
const wsPending = new Map();                            // id -> { method, t0 }
const wsSubs = { block: false, coins: new Set(), phs: new Set() };
let wsEventCount = 0;
let wsSentCount = 0;

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return proto + '//' + location.host + '/ws';
}

function wsBuildMethodsSelect() {
  const sel = document.getElementById('wsRpcMethod');
  sel.innerHTML = WS_METHODS.map(m => '<option value="' + m + '">' + m + '</option>').join('');
}

function wsLog(dir, channel, data, cls) {
  const feed = document.getElementById('wsFeed');
  const ts = new Date();
  const hh = String(ts.getHours()).padStart(2, '0');
  const mm = String(ts.getMinutes()).padStart(2, '0');
  const ss = String(ts.getSeconds()).padStart(2, '0');
  const ms = String(ts.getMilliseconds()).padStart(3, '0');
  const el = document.createElement('div');
  el.className = 'ws-evt ' + (dir === 'in' ? 'in' : dir === 'out' ? 'out' : dir === 'err' ? 'err' : '');
  const chClass = cls || (
    channel.startsWith('block')        ? 'block'   :
    channel.startsWith('coin.spent')   ? 'spent'   :
    channel.startsWith('coin.created') ? 'created' :
    channel.startsWith('coin.mempool') ? 'mempool' :
    channel === '→'                    ? 'sent'    :
    channel === '✗'                    ? 'err'     :
                                         'rpc'
  );
  el.innerHTML = '<span class="ws-ts">' + hh + ':' + mm + ':' + ss + '.' + ms + '</span>' +
                 '<span class="ws-ch ' + chClass + '">' + channel + '</span>' +
                 '<span class="ws-data"></span>';
  el.querySelector('.ws-data').textContent = typeof data === 'string' ? data : JSON.stringify(data);
  feed.appendChild(el);
  while (feed.childElementCount > 500) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
  wsUpdateStats();
}

function wsUpdateStats() {
  document.getElementById('wsStats').textContent = 'sent:' + wsSentCount + ' · events:' + wsEventCount;
}

function wsRenderActive() {
  const box = document.getElementById('wsActive');
  let html = '';
  if (wsSubs.block) {
    html += '<div><b>block</b> · <button onclick="wsSubBlock(false)" class="ws-btn" style="padding:2px 6px;margin-left:4px">x</button></div>';
  }
  if (wsSubs.coins.size) {
    html += '<div style="margin-top:4px"><b>coins (' + wsSubs.coins.size + '):</b></div>';
    for (const c of wsSubs.coins) {
      html += '<span class="chip">' + c.substring(0, 14) + '…<button onclick="wsRemoveCoin(\'' + c + '\')">×</button></span>';
    }
  }
  if (wsSubs.phs.size) {
    html += '<div style="margin-top:4px"><b>puzzle_hashes (' + wsSubs.phs.size + '):</b></div>';
    for (const p of wsSubs.phs) {
      html += '<span class="chip">' + p.substring(0, 14) + '…<button onclick="wsRemovePh(\'' + p + '\')">×</button></span>';
    }
  }
  box.innerHTML = html || 'none';
}

function wsToggleConn() {
  if (ws && ws.readyState === WebSocket.OPEN) { ws.close(); return; }
  const url = wsUrl();
  document.getElementById('wsUrl').textContent = url;
  wsLog('out', '↯ connect', url, 'sent');
  ws = new WebSocket(url);
  ws.onopen = () => {
    document.getElementById('wsDot').className = 'dot';
    document.getElementById('wsConnBtn').textContent = 'Disconnect';
    document.getElementById('wsConnBtn').className = 'ws-btn danger';
    wsLog('in', '↯ open', 'connection established', 'sent');
  };
  ws.onclose = () => {
    document.getElementById('wsDot').className = 'dot off';
    document.getElementById('wsConnBtn').textContent = 'Connect';
    document.getElementById('wsConnBtn').className = 'ws-btn primary';
    wsLog('err', '↯ close', 'connection closed', 'err');
  };
  ws.onerror = () => wsLog('err', '✗', 'ws error');
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { wsLog('err', '✗', 'invalid json: ' + ev.data); return; }
    if (Array.isArray(msg)) { msg.forEach(m => wsHandleIncoming(m)); return; }
    wsHandleIncoming(msg);
  };
}

function wsHandleIncoming(msg) {
  if (msg.method === 'event') {
    wsEventCount++;
    const p = msg.params || {};
    wsLog('in', p.channel || 'event', p.data);
    return;
  }
  if ('id' in msg) {
    const pending = wsPending.get(msg.id);
    wsPending.delete(msg.id);
    const methodLabel = pending ? pending.method : 'response';
    if (msg.error) wsLog('err', '← ' + methodLabel, msg.error, 'err');
    else wsLog('in', '← ' + methodLabel, msg.result);
    return;
  }
  wsLog('in', '← ?', msg);
}

function wsSend(method, params) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    wsLog('err', '✗', 'not connected — click Connect first');
    return null;
  }
  const id = ++wsReqId;
  wsPending.set(id, { method, t0: performance.now() });
  const payload = { jsonrpc: '2.0', id, method, params: params || {} };
  ws.send(JSON.stringify(payload));
  wsSentCount++;
  wsLog('out', '→ ' + method, params || {}, 'sent');
  return id;
}

function wsSubBlock(on) {
  wsSend(on ? 'subscribe_block' : 'unsubscribe_block', {});
  wsSubs.block = on;
  wsRenderActive();
}

function wsParseHexList(raw) {
  return raw
    .split(/[\s,;]+/)
    .map(s => s.trim())
    .filter(Boolean)
    .map(s => s.startsWith('0x') ? s.toLowerCase() : '0x' + s.toLowerCase());
}

function wsSubCoins(on) {
  const ids = wsParseHexList(document.getElementById('wsCoinIds').value);
  if (!ids.length && on) { wsLog('err', '✗', 'enter at least one coin_id'); return; }
  wsSend(on ? 'subscribe_coins' : 'unsubscribe_coins', ids.length ? { coin_ids: ids } : {});
  if (on) ids.forEach(x => wsSubs.coins.add(x));
  else if (ids.length) ids.forEach(x => wsSubs.coins.delete(x));
  else wsSubs.coins.clear();
  wsRenderActive();
}

function wsRemoveCoin(id) {
  wsSend('unsubscribe_coins', { coin_ids: [id] });
  wsSubs.coins.delete(id);
  wsRenderActive();
}

function wsSubPHs(on) {
  const phs = wsParseHexList(document.getElementById('wsPHs').value);
  if (!phs.length && on) { wsLog('err', '✗', 'enter at least one puzzle_hash'); return; }
  wsSend(on ? 'subscribe_puzzle_hashes' : 'unsubscribe_puzzle_hashes', phs.length ? { puzzle_hashes: phs } : {});
  if (on) phs.forEach(x => wsSubs.phs.add(x));
  else if (phs.length) phs.forEach(x => wsSubs.phs.delete(x));
  else wsSubs.phs.clear();
  wsRenderActive();
}

function wsRemovePh(p) {
  wsSend('unsubscribe_puzzle_hashes', { puzzle_hashes: [p] });
  wsSubs.phs.delete(p);
  wsRenderActive();
}

function wsInvoke() {
  const method = document.getElementById('wsRpcMethod').value;
  let params = {};
  const raw = document.getElementById('wsRpcParams').value.trim();
  if (raw) {
    try { params = JSON.parse(raw); }
    catch (e) { wsLog('err', '✗', 'invalid params JSON: ' + e.message); return; }
  }
  wsSend(method, params);
}

function wsClearFeed() {
  document.getElementById('wsFeed').innerHTML = '';
  wsEventCount = 0;
  wsSentCount = 0;
  wsUpdateStats();
}

// ── init ──────────────────────────────────────────────
document.getElementById('reqBody').addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') restSend();
});
document.getElementById('wsUrl').textContent = wsUrl();
buildNav();
wsBuildMethodsSelect();
checkHealth();
setInterval(checkHealth, 5000);
