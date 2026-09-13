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
    && (pip3 install --break-system-packages --no-cache-dir psycopg2-binary || pip3 install --no-cache-dir psycopg2-binary || true)

# Scripts de persistencia (guardan TODO en PostgreSQL)
COPY db_tools/ /usr/local/bin/
RUN chmod +x /usr/local/bin/db

USER alpine

EXPOSE 6080
