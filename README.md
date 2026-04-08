# Chia Simulator Docker

Local Chia blockchain simulator with a coinset.org-compatible HTTP API. Starts in seconds with pre-baked plots and keys.

## Quick Start

```bash
docker compose up -d
# Ready in ~15 seconds:
curl http://localhost:3000/healthz
```

## Web UI

Open `http://localhost:3000` for an interactive API playground.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `BLOCK_INTERVAL` | `5` | Seconds between blocks. `0` = instant confirmation on push_tx |
| `FARM_ADDRESS` | _(built-in)_ | Override bech32 address for farming rewards |

### Block Interval Behavior

| Value | Mode | Mempool |
|---|---|---|
| `0` | Auto-farm: confirms instantly when a tx arrives | Empty |
| `5` | Periodic: farms every 5 seconds | Tx waits ~5s |
| `30` | Periodic: farms every 30 seconds | Tx waits ~30s |

Change at runtime:
```bash
curl -X POST http://localhost:3000/set_config \
  -H "Content-Type: application/json" \
  -d '{"block_interval": 10}'
```

## Endpoints

### Coinset-compatible (same as coinset.org)
- `POST /get_blockchain_state`
- `POST /get_network_info`
- `POST /get_coin_record_by_name`
- `POST /get_coin_records_by_puzzle_hash`
- `POST /get_coin_records_by_puzzle_hashes`
- `POST /get_coin_records_by_parent_ids`
- `POST /get_coin_records_by_hint`
- `POST /get_coin_records_by_names`
- `POST /get_puzzle_and_solution`
- `POST /get_block_record_by_height`
- `POST /get_additions_and_removals`
- `POST /get_mempool_item_by_tx_id`
- `POST /push_tx`
- `POST /get_fee_estimate`

### Simulator-only
- `POST /farm_block` — Farm a block to an address
- `POST /fund_wallet` — Fund a wallet with N XCH
- `POST /set_auto_farming` — Toggle auto-farming directly
- `POST /revert_blocks` — Revert last N blocks
- `POST /get_all_puzzle_hashes` — All puzzle hashes with balances

### Config & Monitoring
- `POST /get_config` — Current simulator configuration
- `POST /set_config` — Update block interval at runtime
- `GET /logs?lines=50&level=ERROR` — View simulator logs
- `GET /healthz` — Health check with current block height

## Persistent Data

By default each container start uses a fresh blockchain. To persist data across restarts, uncomment the volume in `docker-compose.yml`:

```yaml
volumes:
  - chia-sim-data:/root/.chia
```

## Network

- Network: `simulator0`
- Address prefix: `txch`
- Same RPC API as Chia mainnet fullnode
- Port 3000: HTTP API + Web UI
