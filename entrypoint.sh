#!/bin/bash
set -e

# Crear workspace si no existe
mkdir -p "$HOME/workspace"
mkdir -p "$HOME/.local/bin"

# Asegurar que el directorio de la app pertenece al usuario
cd /app 2>/dev/null || true

echo "[entrypoint] arrancando Bebo Console en puerto ${PORT:-3000}"
exec node /app/server.js
