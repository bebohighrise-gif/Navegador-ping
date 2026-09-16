FROM node:20-alpine

RUN apk add --no-cache \
    bash \
    tmux \
    python3 \
    py3-pip \
    py3-psycopg2 \
    postgresql-client \
    curl \
    wget \
    nano \
    vim \
    git \
    sudo \
    make \
    g++ \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev \
    openjdk17-jre \
    php \
    php-cli \
    ruby \
    go \
    unzip \
    tar \
    zip \
    htop \
    coreutils \
    findutils \
    grep \
    sed \
    gawk \
    iproute2 \
    && adduser -D -s /bin/bash desktop \
    && echo "desktop ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && mkdir -p /home/desktop/workspace /home/desktop/.local/bin \
    && chown -R desktop:desktop /home/desktop

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY server.js workspace_api.js ./
COPY public/ ./public/
COPY entrypoint.sh /entrypoint.sh
COPY db_tools/ /usr/local/bin/

RUN chmod +x /entrypoint.sh /usr/local/bin/db /usr/local/bin/autosave /usr/local/bin/purge_session /usr/local/bin/purge_session.py /usr/local/bin/restore_workspace.py \
    && chown -R desktop:desktop /app

USER desktop
WORKDIR /home/desktop

ENV PORT=8080 \
    HOME=/home/desktop \
    PATH="/home/desktop/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    AUTOSAVE_INTERVAL=120 \
    LANG=C.UTF-8

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
