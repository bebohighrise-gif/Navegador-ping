# Base oficial de Ubuntu ligera y estable
FROM ubuntu:24.04

# Evitar bloqueos interactivos durante la instalación de paquetes
ENV DEBIAN_FRONTEND=noninteractive

# 1. Instalar herramientas base del sistema, certificados y dependencias de red
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
    gnupg \
    openssh-client \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# 2. CONFIGURACIÓN OFICIAL DE NODESOURCE (Para Node.js v20 LTS)
# Descargamos la clave GPG oficial e inyectamos el repositorio correcto para Ubuntu Noble (24.04)
RUN mkdir -p /usr/share/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://nodesource.com nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g npx

# 3. Instalar el cliente CLI nativo de PostgreSQL para la persistencia
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# 4. Instalar Python 3, Pip y entornos de ejecución de pruebas exigidos por Bebo
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv python3-pytest \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Establecer el directorio de trabajo en la nube
WORKDIR /workspace

# Iniciar tmux para habilitar multitarea y levantar el servidor HTTP obligatorio para mantener vivo a Render
CMD tmux new-session -d -s nexhost_session && python3 -m http.server ${PORT:-8080}
