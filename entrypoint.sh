#!/bin/bash
set -e

AUTO_FARM=${AUTO_FARM:-true}
BLOCK_INTERVAL=${BLOCK_INTERVAL:-0}

# Clean stale state from previous runs
chia stop all -d 2>/dev/null || true
rm -f "$CHIA_ROOT/run/"*.pid 2>/dev/null
rm -rf /tmp/chia-daemon-* 2>/dev/null
sleep 1

# Write runtime config for API
cat > /tmp/sim_runtime.json <<EOJSON
{"block_interval": $BLOCK_INTERVAL, "auto_farm": $AUTO_FARM}
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

# Configure auto-farm
if [ "$AUTO_FARM" = "true" ]; then
    chia rpc full_node set_auto_farming '{"auto_farm": true}' 2>/dev/null || true
else
    chia rpc full_node set_auto_farming '{"auto_farm": false}' 2>/dev/null || true
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

# Start coinset API
uvicorn api:app --host 0.0.0.0 --port 3000 --app-dir / &

echo "=== Chia Simulator ready ==="
echo "    API:          http://localhost:3000"
echo "    RPC:          https://localhost:8555"
echo "    Auto-farm:    $AUTO_FARM"
echo "    Block interval: ${BLOCK_INTERVAL}s"
echo "    Farm address: $FARM_ADDR"

tail -f "$CHIA_ROOT/log/debug.log"
