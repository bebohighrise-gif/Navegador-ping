FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Dependencias de sistema + Chromium + Node 20 + herramientas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    tmux \
    bubblewrap \
    python3 \
    python3-pip \
    python3-venv \
    postgresql-client \
    chromium \
    chromium-sandbox \
    fonts-liberation \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2t64 \
    libpango-1.0-0 \
    libcairo2 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && ln -sf /usr/bin/chromium /usr/bin/chromium-browser || true

# Python deps
COPY requirements.txt /tmp/requirements.txt
RUN /usr/bin/python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Node deps (mejor cache de capas)
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev 2>/dev/null || npm install --omit=dev

# Código de la aplicación
COPY . /workspace

# Usuario no-root (recomendado). El PTY y bwrap siguen funcionando.
RUN useradd -m -u 10001 -s /bin/bash appuser \
    && mkdir -p /workspace/proyectos \
    && chown -R appuser:appuser /workspace

# Script de arranque robusto
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/ || exit 1

USER appuser

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
