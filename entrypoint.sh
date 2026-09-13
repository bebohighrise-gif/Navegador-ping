#!/bin/bash
set -e

# Crear workspace si no existe
mkdir -p "$HOME/workspace"
mkdir -p "$HOME/.local/bin"

# Arrancar autosave en background si hay DATABASE_URL
if [ -n "$DATABASE_URL" ] && [ -x /usr/local/bin/autosave ]; then
  echo "[entrypoint] iniciando autosave (intervalo ${AUTOSAVE_INTERVAL:-120}s)"
  /usr/local/bin/autosave &
fi

# Asegurar que el directorio de la app pertenece al usuario
cd /app 2>/dev/null || true

echo "[entrypoint] arrancando Bebo Console en puerto ${PORT:-8080}"
exec node /app/server.js
