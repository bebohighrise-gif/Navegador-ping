FROM node:20-alpine

RUN apk add --no-cache \
    bash \
    python3 \
    py3-pip \
    postgresql-client \
    curl \
    nano \
    git \
    sudo \
    && adduser -D -s /bin/bash desktop \
    && echo "desktop ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY server.js ./
COPY public/ ./public/

# Script de persistencia
COPY db_tools/db /usr/local/bin/db
RUN chmod +x /usr/local/bin/db

USER desktop
WORKDIR /home/desktop

ENV PORT=8080
EXPOSE 8080

CMD ["node", "/app/server.js"]
