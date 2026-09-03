#!/usr/bin/env bash
set -euo pipefail

echo "[ENTRYPOINT] Iniciando Bebo AI Host..."

# Arrancar el servidor PTY en background
python3 /workspace/server.py &
PTY_PID=$!

# Arrancar el router HTTP/WS
node /workspace/router.js &
ROUTER_PID=$!

cleanup() {
  echo "[ENTRYPOINT] Señal recibida, deteniendo procesos..."
  kill -TERM "$PTY_PID" "$ROUTER_PID" 2>/dev/null || true
  wait "$PTY_PID" "$ROUTER_PID" 2>/dev/null || true
  exit 0
}

trap cleanup SIGTERM SIGINT

# Esperar a que cualquiera de los dos muera
wait -n "$PTY_PID" "$ROUTER_PID"
EXIT_CODE=$?

echo "[ENTRYPOINT] Un proceso terminó con código $EXIT_CODE. Cerrando el resto..."
cleanup
exit $EXIT_CODE
