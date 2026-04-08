#!/bin/bash
set -e

AUTO_FARM=${AUTO_FARM:-true}
BLOCK_INTERVAL=${BLOCK_INTERVAL:-10}

# Limpiar estado stale de ejecuciones anteriores
echo "Limpiando estado anterior..."
chia stop all -d 2>/dev/null || true
rm -f "$CHIA_ROOT/run/"*.pid 2>/dev/null
rm -rf /tmp/chia-daemon-* 2>/dev/null
sleep 1

# Primera vez: crear el simulador
if [ ! -f "$CHIA_ROOT/config/config.yaml" ]; then
    echo "Generando key..."
    CHIA_ROOT=/tmp/chia-temp chia init >/dev/null 2>&1
    CHIA_ROOT=/tmp/chia-temp chia keys generate --label simulator
    rm -rf /tmp/chia-temp

    echo "Creando simulador..."
    echo "1" | chia dev sim create -a "$AUTO_FARM"
fi

# Escribir config de runtime para que el API pueda leerla/modificarla
CONFIG_FILE="/tmp/sim_runtime.json"
cat > "$CONFIG_FILE" <<EOJSON
{"block_interval": $BLOCK_INTERVAL, "auto_farm": $AUTO_FARM}
EOJSON

# Arrancar simulador
echo "Arrancando simulador..."
chia dev sim start

# Esperar que el RPC responda
echo "Esperando full_node RPC..."
for i in $(seq 1 90); do
    if chia rpc full_node get_blockchain_state 2>/dev/null | grep -q '"success": true'; then
        echo "Full node RPC listo!"
        break
    fi
    if [ "$i" -eq 90 ]; then
        echo "Timeout esperando full_node RPC"
        cat "$CHIA_ROOT/log/debug.log" 2>/dev/null | tail -50
        exit 1
    fi
    sleep 2
done

# Configurar auto-farm
if [ "$AUTO_FARM" = "true" ]; then
    chia rpc full_node set_auto_farming '{"auto_farm": true}' 2>/dev/null || true
fi

# Obtener bech32 address para farm
FARM_ADDR=$(python3 -c "
import yaml, os
root = os.environ.get('CHIA_ROOT', '/root/.chia/simulator/main')
with open(f'{root}/config/config.yaml') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('farmer',{}).get('xch_target_address',''))
" 2>/dev/null || echo "")

echo "Farm address: $FARM_ADDR"

# Farm periódico en background — lee interval de runtime config
(
    while true; do
        INTERVAL=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('block_interval',0))" 2>/dev/null || echo "0")
        if [ "$INTERVAL" -gt 0 ] 2>/dev/null && [ -n "$FARM_ADDR" ]; then
            sleep "$INTERVAL"
            chia rpc full_node farm_block '{"address":"'"$FARM_ADDR"'","guarantee_tx_block":true}' 2>/dev/null || true
        else
            sleep 5
        fi
    done
) &
echo "Farm periódico iniciado (interval: ${BLOCK_INTERVAL}s)"

# Arrancar coinset API
echo "Arrancando coinset API en :3000..."
uvicorn api:app --host 0.0.0.0 --port 3000 --app-dir / &

RPC_PORT=$(python3 -c "
import yaml, os
root = os.environ.get('CHIA_ROOT', '/root/.chia/simulator/main')
with open(f'{root}/config/config.yaml') as f:
    cfg = yaml.safe_load(f)
print(cfg['full_node']['rpc_port'])
" 2>/dev/null)

echo "=== Chia Simulator listo ==="
echo "    coinset API:    http://localhost:3000"
echo "    Full Node RPC:  https://localhost:$RPC_PORT"
echo "    Auto-farm:      $AUTO_FARM"
echo "    Block interval: ${BLOCK_INTERVAL}s"

tail -f "$CHIA_ROOT/log/debug.log"
