# Chia Simulator Docker

Local Chia blockchain simulator with a coinset.org-compatible HTTP API. Starts in seconds with pre-baked plots and keys.

## Quick Start

```bash
docker-compose up -d
# Ready in ~15 seconds:
curl http://localhost:3000/healthz
```

## Web UI

Open `http://localhost:3000` for an interactive API playground.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AUTO_FARM` | `true` | Auto-farm a block when a transaction arrives |
| `BLOCK_INTERVAL` | `0` | Seconds between periodic block farms (0 = disabled) |
| `FARM_ADDRESS` | _(built-in)_ | Override bech32 address for farming rewards |

## Endpoints

### Coinset-compatible (same as coinset.org)
- `POST /get_blockchain_state`
- `POST /get_network_info`
- `POST /get_coin_record_by_name`
- `POST /get_coin_records_by_puzzle_hash`
- `POST /get_coin_records_by_hint`
- `POST /push_tx`
- `POST /get_fee_estimate`
- [and more...]

### Simulator-only
- `POST /farm_block` — Farm a block to an address
- `POST /fund_wallet` — Fund a wallet with N XCH
- `POST /set_auto_farming` — Toggle auto-farming
- `POST /revert_blocks` — Revert last N blocks
- `POST /get_all_puzzle_hashes` — All puzzle hashes with balances

### Config
- `POST /get_config` — Current simulator configuration
- `POST /set_config` — Update block_interval and auto_farm at runtime

### Health
- `GET /healthz` — Health check with current block height

## Persistent Data

By default, each container start uses the pre-baked blockchain. To persist data across restarts, uncomment the volume in `docker-compose.yml`:

```yaml
volumes:
  - chia-sim-data:/root/.chia
```

## Ports

| Port | Protocol | Description |
|---|---|---|
| 3000 | HTTP | API + Web UI |
| 8555 | HTTPS | Full Node RPC (requires SSL certs from container) |

## Network

- Network: `simulator0`
- Address prefix: `txch`
- Same RPC API as Chia mainnet fullnode
