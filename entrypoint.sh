#!/bin/bash
set -e

export HOME=/home/desktop
cd /home/desktop
mkdir -p /home/desktop/workspace /home/desktop/.local/bin

echo "[bebo] Arrancando consola..."

if [ -n "$DATABASE_URL" ]; then
  echo "[bebo] Inicializando persistencia..."
  python3 /usr/local/bin/db init 2>/dev/null || true
  echo "[bebo] Restaurando estado desde PostgreSQL..."
  python3 /usr/local/bin/db restore 2>/dev/null || true

  # Autosave en background
  python3 /usr/local/bin/autosave &
  echo "[bebo] Autosave activo (cada ${AUTOSAVE_INTERVAL:-120}s)"
else
  echo "[bebo] AVISO: sin DATABASE_URL — no habrá persistencia"
fi

# Prompt agradable
if [ ! -f /home/desktop/.bashrc ]; then
  cat > /home/desktop/.bashrc << 'EOF'
export PS1='\[\e[1;32m\]desktop@beboai\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]$ '
export PATH="$HOME/.local/bin:$PATH"
alias ll='ls -la'
alias workspace='cd ~/workspace'
cd ~/workspace 2>/dev/null || true
EOF
fi

exec node /app/server.js
