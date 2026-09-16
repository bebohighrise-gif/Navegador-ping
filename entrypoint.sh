#!/bin/bash
set -e

# Crear workspace si no existe
mkdir -p "$HOME/workspace"
mkdir -p "$HOME/.local/bin"

# Restaurar workspace desde la DB (disco de Render es efímero → todo nace de datos)
if [ -n "$DATABASE_URL" ] && [ -x /usr/local/bin/restore_workspace.py ]; then
  echo "[entrypoint] restaurando workspace desde DATABASE_URL..."
  python3 /usr/local/bin/restore_workspace.py || echo "[entrypoint] restore falló (continuando)"
fi

# Arrancar autosave en background si hay DATABASE_URL
if [ -n "$DATABASE_URL" ] && [ -x /usr/local/bin/autosave ]; then
  echo "[entrypoint] iniciando autosave (intervalo ${AUTOSAVE_INTERVAL:-120}s, max 3 snapshots)"
  /usr/local/bin/autosave &
fi

# Asegurar que el directorio de la app pertenece al usuario
cd /app 2>/dev/null || true

echo "[entrypoint] arrancando Bebo Console en puerto ${PORT:-8080}"
exec node /app/server.js
