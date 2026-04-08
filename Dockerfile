# Stage 1: Build chia-blockchain and pre-bake simulator
FROM python:3.11-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential cmake \
    libgmp-dev libffi-dev libssl-dev \
    python3-dev python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /chia
RUN git clone --depth 1 --branch latest https://github.com/Chia-Network/chia-blockchain.git .
RUN /bin/bash install.sh
RUN /chia/venv/bin/pip install --no-cache-dir fastapi uvicorn

ENV PATH="/chia/venv/bin:$PATH"
ENV CHIA_ROOT="/root/.chia/simulator/main"

# Pre-bake: generate key and create simulator (includes plot generation)
RUN CHIA_ROOT=/tmp/chia-temp chia init >/dev/null 2>&1 \
    && CHIA_ROOT=/tmp/chia-temp chia keys generate --label simulator \
    && echo "1" | chia dev sim create -a true \
    && rm -rf /tmp/chia-temp

# Patch config: fixed RPC port + fixed daemon port (sim generates random ones)
RUN python3 -c "\
import yaml; \
p='${CHIA_ROOT}/config/config.yaml'; \
c=yaml.safe_load(open(p)); \
c['full_node']['rpc_port']=8555; \
c['daemon_port']=55400; \
yaml.dump(c,open(p,'w'))"

# Remove DB so it rebuilds cleanly on first start (keeps config, plots, keys)
RUN rm -rf ${CHIA_ROOT}/db/ ${CHIA_ROOT}/run/ ${CHIA_ROOT}/log/

# Strip build artifacts
RUN rm -rf /chia/.git /chia/tests /chia/benchmarks /chia/build_scripts \
    && find /chia -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true

# Stage 2: Runtime image (no build tools)
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libgmp10 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /chia /chia
COPY --from=builder /root/.chia /root/.chia
COPY --from=builder /root/.chia_keys /root/.chia_keys

ENV PATH="/chia/venv/bin:$PATH"
ENV CHIA_ROOT="/root/.chia/simulator/main"
ENV AUTO_FARM="true"
ENV BLOCK_INTERVAL="0"
ENV FARM_ADDRESS=""

COPY api.py /api.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 3000 8555

HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=10 \
  CMD curl -sf http://localhost:3000/healthz || exit 1

LABEL org.opencontainers.image.source="https://github.com/chia-sim-docker" \
      org.opencontainers.image.description="Chia blockchain simulator with coinset.org-compatible API" \
      org.opencontainers.image.title="chia-sim"

CMD ["/entrypoint.sh"]
