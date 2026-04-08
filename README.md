# Chia Simulator Docker

Local Chia blockchain simulator for development and testing.

## Quick Start

```bash
cd chia-sim-docker
docker-compose up -d
# Wait ~2 minutes for first-run plot generation
docker exec chia-sim chia rpc full_node get_blockchain_state
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AUTO_FARM` | `true` | Auto-farm a block when a tx arrives |
| `PLOT_COUNT` | `1` | Number of k19 plots to create (first run only) |
| `FARM_ADDRESS` | _(auto)_ | Bech32 address for farming rewards |
| `BLOCK_INTERVAL` | _(none)_ | Seconds between periodic block farms |

## Ports

- **8555** — Full Node RPC (HTTPS)
- **9256** — Wallet RPC (HTTPS)

## Usage Examples

Farm a block manually:
```bash
docker exec chia-sim chia rpc full_node farm_block '{"address":"<your_address>"}'
```

Check blockchain state:
```bash
docker exec chia-sim chia rpc full_node get_blockchain_state
```

View logs:
```bash
docker logs -f chia-sim
```

## SSL Certificates

The RPC uses self-signed certs located inside the container at:
```
/root/.chia/simulator/main/config/ssl/full_node/
```

To call the RPC from the host with curl:
```bash
docker cp chia-sim:/root/.chia/simulator/main/config/ssl/full_node/private_full_node.crt /tmp/
docker cp chia-sim:/root/.chia/simulator/main/config/ssl/full_node/private_full_node.key /tmp/
curl -sk -X POST https://localhost:8555/get_blockchain_state \
  -d '{}' --cert /tmp/private_full_node.crt --key /tmp/private_full_node.key
```

## Reset

```bash
docker-compose down -v
docker-compose up -d
```
