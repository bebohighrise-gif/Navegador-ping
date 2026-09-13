FROM alpine:3.20

# Paquetes mínimos para escritorio + VNC + noVNC
RUN apk add --no-cache \
    bash \
    sudo \
    curl \
    wget \
    python3 \
    py3-numpy \
    fluxbox \
    xterm \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    font-dejavu \
    dbus-x11 \
    && adduser -D -s /bin/bash desktop \
    && echo "desktop ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && mkdir -p /home/desktop/.vnc /home/desktop/.fluxbox \
    && chown -R desktop:desktop /home/desktop

# Configuración de Fluxbox (menú simple)
RUN echo 'session.screen0.toolbar.visible: true' > /home/desktop/.fluxbox/init \
    && echo '[begin] (Fluxbox)' > /home/desktop/.fluxbox/menu \
    && echo '  [exec] (Terminal) {xterm}' >> /home/desktop/.fluxbox/menu \
    && echo '  [exit] (Salir)' >> /home/desktop/.fluxbox/menu \
    && echo '[end]' >> /home/desktop/.fluxbox/menu \
    && chown -R desktop:desktop /home/desktop

# Script de arranque
COPY start.sh /start.sh
RUN chmod +x /start.sh

USER desktop
WORKDIR /home/desktop
ENV DISPLAY=:1
ENV HOME=/home/desktop

EXPOSE 6080

CMD ["/start.sh"]
