FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Instalar dependencias base, configurar repositorio de Node.js e instalar Node.js en una sola capa
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl wget git unzip tar gcc make ca-certificates gnupg \
    openssh-client tmux postgresql-client python3 python3-pip \
    python3-venv python3-pytest \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npx \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

RUN useradd -m -u 1001 -s /bin/bash renderuser && \
    mkdir -p /workspace && chown renderuser:renderuser /workspace

WORKDIR /workspace
USER renderuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:$PORT/ || exit 1

EXPOSE $PORT
CMD ["python3", "-m", "http.server", "8080"]
