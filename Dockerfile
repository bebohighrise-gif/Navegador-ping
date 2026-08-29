# Base oficial de Ubuntu ligera y estable
FROM ubuntu:24.04

# Evitar bloqueos interactivos durante la instalación
ENV DEBIAN_FRONTEND=noninteractive

# 1. Instalar herramientas del sistema requeridas por Bebo y red en tiempo real
RUN apt-get update && apt-get install -y \
    bash \
    curl \
    wget \
    git \
    unzip \
    tar \
    gcc \
    make \
    ca-certificates \
    openssh-client \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# 2. Instalar el cliente CLI nativo de PostgreSQL para la persistencia
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# 3. Instalar Python 3, Pip y entornos de ejecución de pruebas exigidos
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv python3-pytest \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# 4. Instalar Node.js LTS y NPM de forma nativa
RUN curl -fsSL https://nodesource.com | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g npx

# Establecer la ruta donde Bebo ejecutará y editará los archivos en tiempo real
WORKDIR /workspace

# Iniciar tmux para habilitar multitarea y levantar el servidor HTTP obligatorio para Render
CMD tmux new-session -d -s nexhost_session && python3 -m http.server ${PORT:-8080}
