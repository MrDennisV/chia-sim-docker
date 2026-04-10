# Chia Simulator Docker

Local Chia blockchain simulator with full Goby wallet backend and coinset.org-compatible HTTP API. Starts in seconds with pre-baked plots and keys.

## Quick Start

```bash
docker compose up -d
# Ready in ~15 seconds:
curl http://localhost:3000/healthz
```

## Web UI

Open `http://localhost:3000` for an interactive API playground with all endpoints.

## Goby Wallet

Compatible with the [Goby](https://www.goby.app/) Chrome extension. Full wallet functionality:

- View XCH balance
- Send XCH transactions
- View UTXOs / coins
- Estimate fees
- NFT/DID indexing (via built-in watcher)

### Connecting Goby

> **Important**: Goby does not accept `localhost`. Use your LAN IP or a domain with SSL.

1. Find your LAN IP (e.g. `192.168.1.100`)
2. In Goby extension settings, set the RPC URL to: `http://192.168.1.100:3000`
3. Select the network in Goby that matches your `NETWORK_MODE`:
   - `testnet11` → **Testnet** (address prefix `txch`)
   - `mainnet` → **Mainnet** (address prefix `xch`)

For production/remote access, put the API behind a reverse proxy with SSL (e.g. nginx + Let's Encrypt).

### Fund a test wallet

Copy your Goby address and run:
```bash
curl -X POST http://192.168.1.100:3000/fund_wallet \
  -H "Content-Type: application/json" \
  -d '{"address": "xch1...", "amount": 100}'
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `NETWORK_MODE` | `testnet11` | Network identity: `testnet11` (txch) or `mainnet` (xch) |
| `BLOCK_INTERVAL` | `5` | Seconds between blocks. `0` = instant confirmation on push_tx |
| `FARM_ADDRESS` | _(built-in)_ | Override bech32 address for farming rewards |

### Network Mode

The simulator can identify as either **testnet11** or **mainnet**. This controls the genesis challenge, address prefix, and network name reported to wallets.

```yaml
environment:
  NETWORK_MODE: "mainnet"    # or "testnet11"
```

| Mode | Prefix | Goby network |
|---|---|---|
| `testnet11` | `txch` | Testnet |
| `mainnet` | `xch` | Mainnet |

> **Note**: Switching modes on an existing volume automatically wipes the blockchain DB, since the genesis challenge is incompatible between modes.

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
- `POST /get_block_record`
- `POST /get_block` / `get_blocks`
- `POST /get_block_spends`
- `POST /get_additions_and_removals`
- `POST /get_mempool_item_by_tx_id`
- `POST /get_all_mempool_tx_ids`
- `POST /get_all_mempool_items`
- `POST /push_tx`
- `POST /get_fee_estimate`
- `POST /get_routes`

### Goby /v1/ (full wallet backend)
- `POST /v1/chia_rpc` — RPC wrapper `{method, params}`
- `GET /v1/utxos?address=xch1...` — Unspent coins
- `GET /v1/balance?address=xch1...` — Total balance
- `POST /v1/sendtx` — Submit spend bundle
- `POST /v1/fee_estimate` — Fee estimates
- `GET /v1/assets?address=xch1...` — NFT/DID assets
- `GET /v1/latest_singleton?singleton_id=0x...` — Singleton tracking

### Simulator-only
- `POST /farm_block` — Farm a block to an address
- `POST /fund_wallet` — Fund a wallet with N XCH
- `POST /revert_blocks` — Revert last N blocks
- `POST /get_all_puzzle_hashes` — All puzzle hashes with balances

### Config & Monitoring
- `POST /get_config` — Current simulator configuration
- `POST /set_config` — Update block interval at runtime
- `GET /logs/node?lines=50&level=ERROR` — Simulator full node logs
- `GET /logs/api?lines=50` — API request logs
- `GET /healthz` — Health check with current block height

## Persistent Data

By default the blockchain data persists across restarts via a Docker volume. If the blockchain gets corrupted after a restart, nuke the volume and start fresh:

```bash
docker compose down -v
docker compose up -d
```

## Network

- Network mode is configurable via `NETWORK_MODE`: **testnet11** (`txch`) or **mainnet** (`xch`)
- The simulator patches its genesis challenge and network identity to match the selected mode, for compatibility with wallets like Goby
- Same RPC API as a Chia fullnode
- Port 3000: HTTP API + Web UI
