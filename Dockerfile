FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential cmake \
    libgmp-dev libffi-dev libssl-dev \
    python3-dev python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /chia
RUN git clone --depth 1 --branch latest https://github.com/Chia-Network/chia-blockchain.git .
RUN /bin/bash install.sh

ENV PATH="/chia/venv/bin:$PATH"
ENV CHIA_ROOT="/root/.chia/simulator/main"

RUN pip install fastapi uvicorn

COPY api.py /api.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 3000 8555

HEALTHCHECK --interval=5s --timeout=5s --retries=30 \
  CMD chia rpc full_node get_blockchain_state 2>/dev/null | grep -q '"success": true' || exit 1

CMD ["/entrypoint.sh"]
