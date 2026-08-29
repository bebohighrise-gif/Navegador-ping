FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium-browser

# Instalar dependencias de sistema, Python, Node.js, PostgreSQL client, Tmux y Chromium para Puppeteer
RUN apt-get update && apt-get install -y \
    curl \
    git \
    tmux \
    python3 \
    python3-pip \
    python3-venv \
    postgresql-client \
    chromium-browser \
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
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Copiar configuración de dependencias e instalarlas
COPY package.json /workspace/package.json
RUN npm install

# Copiar los scripts de la raíz al contenedor
COPY . /workspace

EXPOSE 8080

# Iniciar el Router HTTP y el Servidor WebSocket de la terminal
CMD ["bash", "-c", "node /workspace/router.js & python3 /workspace/server.py"]
