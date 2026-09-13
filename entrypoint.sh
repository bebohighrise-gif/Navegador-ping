#!/bin/bash
set -e

export HOME=/home/desktop
cd /home/desktop

echo "[bebo] Iniciando consola..."

# 1) Inicializar tablas y restaurar todo desde PostgreSQL
if [ -n "$DATABASE_URL" ]; then
  echo "[bebo] Conectando a la base de datos..."
  python3 /usr/local/bin/db init 2>/dev/null || true
  echo "[bebo] Restaurando archivos y estado..."
  python3 /usr/local/bin/db restore 2>/dev/null || true
else
  echo "[bebo] AVISO: No hay DATABASE_URL. Nada se persistirá."
fi

# 2) Arrancar autosave en segundo plano (guarda cada 2 min)
if [ -n "$DATABASE_URL" ]; then
  python3 /usr/local/bin/autosave &
  echo "[bebo] Autosave activo (cada ${AUTOSAVE_INTERVAL:-120}s)"
fi

# 3) Arrancar el servidor de la consola
exec node /app/server.js
