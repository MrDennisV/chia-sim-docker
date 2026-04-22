#!/bin/bash
set -e

BLOCK_INTERVAL=${BLOCK_INTERVAL:-5}
NETWORK_MODE=${NETWORK_MODE:-testnet11}

# Validate NETWORK_MODE
if [ "$NETWORK_MODE" != "testnet11" ] && [ "$NETWORK_MODE" != "mainnet" ]; then
    echo "ERROR: NETWORK_MODE must be 'testnet11' or 'mainnet', got '$NETWORK_MODE'"
    exit 1
fi

# Determine genesis challenge for the selected mode
if [ "$NETWORK_MODE" = "mainnet" ]; then
    GENESIS="ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb"
    NET_PREFIX="xch"
else
    GENESIS="37a90eb5185a9c4439a91ddc98bbadce7b4feba060d50116a067de66bf236615"
    NET_PREFIX="txch"
fi

# Check current genesis in config; if different, wipe incompatible DB
CURRENT_GENESIS=$(python3 -c "
import yaml, os
p = os.environ['CHIA_ROOT'] + '/config/config.yaml'
c = yaml.safe_load(open(p))
print(c.get('network_overrides',{}).get('constants',{}).get('simulator0',{}).get('GENESIS_CHALLENGE',''))
" 2>/dev/null || echo "")

if [ -n "$CURRENT_GENESIS" ] && [ "$CURRENT_GENESIS" != "$GENESIS" ]; then
    echo "Network mode changed (genesis $CURRENT_GENESIS -> $GENESIS). Wiping incompatible DB..."
    rm -rf "$CHIA_ROOT/db/" "$CHIA_ROOT/run/"
    rm -f /app/data/wallet_simulator.db
    echo "DB wiped. Chain will rebuild on start."
fi

# Patch config.yaml with correct genesis challenge
python3 -c "
import yaml, os
p = os.environ['CHIA_ROOT'] + '/config/config.yaml'
c = yaml.safe_load(open(p))
sim = c['network_overrides']['constants']['simulator0']
sim['GENESIS_CHALLENGE'] = '$GENESIS'
sim['AGG_SIG_ME_ADDITIONAL_DATA'] = '$GENESIS'
yaml.dump(c, open(p, 'w'))
"
echo "Genesis challenge set for $NETWORK_MODE mode"

# Patch settings.toml with correct network name and prefix
python3 -c "
import re
mode = '$NETWORK_MODE'
prefix = '$NET_PREFIX'
chain_id = 1 if mode == 'mainnet' else 2
with open('/app/settings.toml') as f:
    content = f.read()
content = re.sub(r'network_name\s*=\s*\"[^\"]*\"', f'network_name = \"{mode}\"', content)
content = re.sub(r'network_prefix\s*=\s*\"[^\"]*\"', f'network_prefix = \"{prefix}\"', content)
content = re.sub(r'^id\s*=\s*\d+', f'id = {chain_id}', content, flags=re.MULTILINE)
with open('/app/settings.toml', 'w') as f:
    f.write(content)
"

# Clean stale state from previous runs
chia stop all -d 2>/dev/null || true
rm -f "$CHIA_ROOT/run/"*.pid 2>/dev/null
rm -rf /tmp/chia-daemon-* 2>/dev/null
sleep 1

# Write runtime config for API
cat > /tmp/sim_runtime.json <<EOJSON
{"block_interval": $BLOCK_INTERVAL, "network_mode": "$NETWORK_MODE"}
EOJSON

# Start simulator
echo "Starting Chia simulator..."
chia dev sim start

# Wait for RPC
echo "Waiting for full_node RPC..."
for i in $(seq 1 60); do
    if chia rpc full_node get_blockchain_state 2>/dev/null | grep -q '"success": true'; then
        echo "Full node RPC ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "Timeout waiting for full_node RPC"
        cat "$CHIA_ROOT/log/debug.log" 2>/dev/null | tail -30
        exit 1
    fi
    sleep 2
done

# Auto-farm logic: interval=0 means auto-farm on push_tx, interval>0 means periodic
if [ "$BLOCK_INTERVAL" -eq 0 ] 2>/dev/null; then
    chia rpc full_node set_auto_farming '{"auto_farm": true}' 2>/dev/null || true
    echo "Mode: auto-farm (instant confirmation on push_tx)"
else
    chia rpc full_node set_auto_farming '{"auto_farm": false}' 2>/dev/null || true
    echo "Mode: periodic farm every ${BLOCK_INTERVAL}s (tx stays in mempool)"
fi

# Prefund addresses on fresh chain only (PREFUND_ADDRESSES=comma-separated list, PREFUND_AMOUNT=XCH per address)
if [ -n "$PREFUND_ADDRESSES" ]; then
    PREFUND_AMOUNT=${PREFUND_AMOUNT:-100}
    CURRENT_HEIGHT=$(chia rpc full_node get_blockchain_state 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    peak = d.get('blockchain_state', {}).get('peak')
    print(peak.get('height', 0) if peak else 0)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

    if [ "$CURRENT_HEIGHT" = "0" ]; then
        BLOCKS_PER_ADDR=$(( (PREFUND_AMOUNT + 1) / 2 ))
        [ "$BLOCKS_PER_ADDR" -lt 1 ] && BLOCKS_PER_ADDR=1
        echo "Prefunding addresses with ${PREFUND_AMOUNT} XCH each (${BLOCKS_PER_ADDR} blocks per address)..."
        IFS=',' read -ra _ADDRS <<< "$PREFUND_ADDRESSES"
        for _addr in "${_ADDRS[@]}"; do
            _addr=$(echo "$_addr" | xargs)
            [ -z "$_addr" ] && continue
            echo "  -> $_addr"
            for _i in $(seq 1 "$BLOCKS_PER_ADDR"); do
                if ! chia rpc full_node farm_block "{\"address\":\"$_addr\",\"guarantee_tx_block\":true}" >/dev/null 2>&1; then
                    echo "     WARN: farm_block failed for $_addr (check prefix matches NETWORK_MODE=$NETWORK_MODE)"
                    break
                fi
            done
        done
        echo "Prefund complete."
    else
        echo "Prefund skipped: chain already has $CURRENT_HEIGHT blocks"
    fi
fi

# Determine farm address: env override or from config
if [ -n "$FARM_ADDRESS" ]; then
    FARM_ADDR="$FARM_ADDRESS"
else
    FARM_ADDR=$(python3 -c "
import yaml, os
with open(os.environ['CHIA_ROOT'] + '/config/config.yaml') as f:
    print(yaml.safe_load(f).get('farmer',{}).get('xch_target_address',''))
" 2>/dev/null || echo "")
fi

# Periodic farm loop (reads interval from runtime config)
(
    while true; do
        INTERVAL=$(python3 -c "import json; print(json.load(open('/tmp/sim_runtime.json')).get('block_interval',0))" 2>/dev/null || echo "0")
        if [ "$INTERVAL" -gt 0 ] 2>/dev/null && [ -n "$FARM_ADDR" ]; then
            sleep "$INTERVAL"
            chia rpc full_node farm_block '{"address":"'"$FARM_ADDR"'","guarantee_tx_block":true}' 2>/dev/null || true
        else
            sleep 5
        fi
    done
) &

# Start Goby watcher (syncs NFTs/DIDs in background)
cd /app
python3 -m openapi.watcher &
echo "Goby watcher started."

# Start API (Goby + Simulator combined)
uvicorn api:app --host 0.0.0.0 --port 3000 &

echo "=== Chia Simulator ready ==="
echo "    API:            http://localhost:3000"
echo "    Network mode:   $NETWORK_MODE ($NET_PREFIX)"
echo "    Block interval: ${BLOCK_INTERVAL}s (0=auto-farm)"
echo "    Farm address:   $FARM_ADDR"

tail -f "$CHIA_ROOT/log/debug.log"
