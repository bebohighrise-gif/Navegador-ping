#!/usr/bin/env python3
"""Restaura el último snapshot de ~/workspace desde PostgreSQL.
Se ejecuta al arrancar el contenedor para que todo nazca de la DB
(Render tiene disco efímero). No llena espacio: solo 1 snapshot activo en disco.
"""
import os
import sys
import tarfile
import io
import base64
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOME = os.environ.get("HOME", "/home/desktop")
WORKSPACE = os.path.join(HOME, "workspace")
DATABASE_URL = os.environ.get("DATABASE_URL")
SESSION_NAME = os.environ.get("TMUX_SESSION_NAME", "bebo")

def psycopg_dsn(url):
    if not url:
        return url
    try:
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() != "uselibpqcompat"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url

def log(msg):
    print(f"[restore] {msg}", flush=True)

def main():
    if not DATABASE_URL:
        log("DATABASE_URL no definida — skip restore")
        return 0

    try:
        import psycopg2
    except ImportError:
        log("psycopg2 no disponible — skip")
        return 0

    try:
        conn = psycopg2.connect(psycopg_dsn(DATABASE_URL), connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bebo_snapshots (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                session_name TEXT NOT NULL DEFAULT 'bebo',
                payload TEXT NOT NULL
            )
        """)
        cur.execute("ALTER TABLE bebo_snapshots ADD COLUMN IF NOT EXISTS session_name TEXT NOT NULL DEFAULT 'bebo'")
        cur.execute("""
            SELECT payload, created_at FROM bebo_snapshots
            WHERE session_name = %s
            ORDER BY created_at DESC LIMIT 1
        """, (SESSION_NAME,))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        log(f"error DB: {e}")
        return 0

    if not row:
        log("no hay snapshots — workspace vacío")
        os.makedirs(WORKSPACE, exist_ok=True)
        return 0

    payload_b64, created_at = row
    log(f"restaurando snapshot de {created_at} ({len(payload_b64)} chars b64)")

    try:
        raw = base64.b64decode(payload_b64)
        # Limpiar workspace actual (solo contenido, no el dir)
        if os.path.isdir(WORKSPACE):
            for name in os.listdir(WORKSPACE):
                if name.startswith("."):
                    continue
                path = os.path.join(WORKSPACE, name)
                try:
                    if os.path.isdir(path):
                        import shutil
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.unlink(path)
                except Exception:
                    pass
        else:
            os.makedirs(WORKSPACE, exist_ok=True)

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            try:
                tar.extractall(WORKSPACE, filter=tarfile.data_filter)
            except (AttributeError, TypeError):
                tar.extractall(WORKSPACE)
        log(f"workspace restaurado en {WORKSPACE}")
    except Exception as e:
        log(f"error al extraer: {e}")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
