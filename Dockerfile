# ============================================================
#  Base: Ubuntu 24.04 (Noble) - Imagen ligera y estable
# ============================================================
FROM ubuntu:24.04

# ------------------------------------------------------------
#  Variables de entorno
# ------------------------------------------------------------
ENV DEBIAN_FRONTEND=noninteractive \
    NODE_VERSION=20 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# ------------------------------------------------------------
#  1. Instalación de dependencias del sistema (incluye tmux)
# ------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    postgresql-client \
    python3 \
    python3-pip \
    python3-venv \
    python3-pytest \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# ------------------------------------------------------------
#  2. Instalación de Node.js 20 LTS (método oficial)
# ------------------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------
#  3. Creación de usuario no root (seguridad)
# ------------------------------------------------------------
RUN useradd -m -u 1001 -s /bin/bash renderuser && \
    mkdir -p /workspace && \
    chown renderuser:renderuser /workspace

# ------------------------------------------------------------
#  4. Configuración del directorio de trabajo
# ------------------------------------------------------------
WORKDIR /workspace
USER renderuser

# ------------------------------------------------------------
#  5. Healthcheck (opcional, útil para Render)
# ------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:$PORT/ || exit 1

# ------------------------------------------------------------
#  6. Comando de inicio (servidor HTTP en primer plano)
#     tmux está instalado pero NO se usa aquí; 
#     puedes ejecutarlo manualmente con "docker exec -it" si lo necesitas.
# ------------------------------------------------------------
EXPOSE $PORT
CMD python3 -m http.server $PORT
