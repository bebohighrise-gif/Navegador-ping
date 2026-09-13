FROM novaspirit/alpine_xfce4_novnc

USER root

# Herramientas necesarias para persistencia con la base de datos
RUN apk add --no-cache \
    postgresql-client \
    python3 \
    py3-pip \
    bash \
    curl \
    nano \
    && pip3 install --break-system-packages --no-cache-dir psycopg2-binary 2>/dev/null || true

# Scripts de persistencia (guardan TODO en PostgreSQL)
COPY db_tools/ /usr/local/bin/
RUN chmod +x /usr/local/bin/db \
    && chmod +x /usr/local/bin/db-* 2>/dev/null || true

# Nota de bienvenida en el escritorio del usuario
RUN mkdir -p /home/alpine/Desktop \
    && echo 'LEE ESTO PRIMERO' > /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo 'Este escritorio se borra cada vez que se reinicia.' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo 'TODO lo importante debe guardarse en la base de datos.' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo 'Comandos disponibles en la terminal:' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '  db init          → crea las tablas en PostgreSQL' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '  db save archivo  → guarda un archivo en la BD' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '  db load archivo  → recupera un archivo de la BD' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '  db list          → lista lo guardado' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '  db pkg add nano  → marca un paquete para reinstalar' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && echo '  db restore       → reinstala paquetes + recupera archivos' >> /home/alpine/Desktop/LEE_ESTO.txt \
    && chown -R alpine:alpine /home/alpine

USER alpine

EXPOSE 6080
