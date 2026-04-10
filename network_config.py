import os

NETWORK_MODE = os.getenv("NETWORK_MODE", "testnet11").lower()

NETWORK_CONFIGS = {
    "testnet11": {
        "genesis_challenge": "37a90eb5185a9c4439a91ddc98bbadce7b4feba060d50116a067de66bf236615",
        "network_name": "testnet11",
        "network_prefix": "txch",
        "address_example": "txch1...",
    },
    "mainnet": {
        "genesis_challenge": "ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb",
        "network_name": "mainnet",
        "network_prefix": "xch",
        "address_example": "xch1...",
    },
}

if NETWORK_MODE not in NETWORK_CONFIGS:
    raise ValueError(f"Invalid NETWORK_MODE '{NETWORK_MODE}'. Must be 'testnet11' or 'mainnet'.")

NET = NETWORK_CONFIGS[NETWORK_MODE]
