#!/bin/bash
set -e

export DISPLAY=:1
export HOME=/home/desktop

# Crear directorio X11 si no existe
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix 2>/dev/null || true

# Arrancar Xvfb (pantalla virtual)
Xvfb :1 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
sleep 1

# Arrancar Fluxbox
fluxbox &
sleep 1

# Arrancar x11vnc (sin contraseña para simplificar)
x11vnc -display :1 -forever -shared -rfbport 5900 -nopw -xkb &
sleep 1

# Arrancar noVNC (escucha en 6080 y hace proxy al VNC local)
# websockify funciona bien detrás del HTTPS de Render
exec websockify --web=/usr/share/novnc/ 6080 localhost:5900
