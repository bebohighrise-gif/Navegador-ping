#!/usr/bin/env python3
"""
Gestiona el despliegue de proyectos: asigna puerto libre, registra en Postgres
y lanza el proceso dentro de una sesión tmux.
"""
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

DATABASE_URL = os.environ.get("DATABASE_URL")
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace/proyectos")).resolve()
PORT_RANGE_START = int(os.environ.get("PORT_RANGE_START", "3001"))
PORT_RANGE_END = int(os.environ.get("PORT_RANGE_END", "8999"))


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está definida")
    return psycopg2.connect(DATABASE_URL)


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS proyectos (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100) UNIQUE NOT NULL,
                subdominio VARCHAR(150) UNIQUE NOT NULL,
                puerto INT NOT NULL,
                comando VARCHAR(255) NOT NULL,
                estado VARCHAR(20) DEFAULT 'activo',
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_proyectos_subdominio_estado
            ON proyectos(subdominio, estado);
            """
        )
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
            """
        )
        cur.execute(
            """
            DROP TRIGGER IF EXISTS update_proyectos_updated_at ON proyectos;
            CREATE TRIGGER update_proyectos_updated_at
            BEFORE UPDATE ON proyectos
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
            """
        )
    conn.commit()


def get_free_port(start_port: int = PORT_RANGE_START) -> int:
    port = start_port
    while port <= PORT_RANGE_END:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"No hay puertos libres entre {start_port} y {PORT_RANGE_END}")


def validate_name(name: str, field: str) -> str:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError(f"{field} inválido (longitud)")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}", name):
        raise ValueError(f"{field} contiene caracteres no permitidos")
    return name


def desplegar_proyecto(nombre_proyecto: str, subdominio: str, comando_inicio: str) -> None:
    nombre_proyecto = validate_name(nombre_proyecto, "nombre")
    subdominio = validate_name(subdominio, "subdominio").lower()

    if not comando_inicio or len(comando_inicio) > 255:
        raise ValueError("comando_inicio inválido")

    puerto = get_free_port()
    path_proyecto = WORKSPACE_ROOT / nombre_proyecto
    path_proyecto.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO proyectos (nombre, subdominio, puerto, comando, estado)
                VALUES (%s, %s, %s, %s, 'activo')
                ON CONFLICT (subdominio)
                DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    puerto = EXCLUDED.puerto,
                    comando = EXCLUDED.comando,
                    estado = 'activo',
                    updated_at = NOW();
                """,
                (nombre_proyecto, subdominio, puerto, comando_inicio),
            )
        conn.commit()
    finally:
        conn.close()

    session_name = f"host_{nombre_proyecto}"
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        stderr=subprocess.DEVNULL,
        check=False,
    )

    exec_cmd = f"cd {path_proyecto} && PORT={puerto} {comando_inicio}"
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "bash", "-c", exec_cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Error al crear sesión tmux: {result.stderr or result.stdout}")

    print(f"✅ Proyecto '{nombre_proyecto}' desplegado correctamente")
    print(f"   - Subdominio : {subdominio}")
    print(f"   - Puerto     : {puerto}")
    print(f"   - Tmux       : {session_name}")
    print(f"   - Path       : {path_proyecto}")


def listar_proyectos() -> None:
    conn = get_db_connection()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nombre, subdominio, puerto, estado, updated_at FROM proyectos ORDER BY nombre"
            )
            rows = cur.fetchall()
        if not rows:
            print("No hay proyectos registrados.")
            return
        print(f"{'NOMBRE':<20} {'SUBDOMINIO':<30} {'PUERTO':<8} {'ESTADO':<10} UPDATED")
        print("-" * 90)
        for nombre, sub, puerto, estado, updated in rows:
            print(f"{nombre:<20} {sub:<30} {puerto:<8} {estado:<10} {updated}")
    finally:
        conn.close()


def detener_proyecto(nombre_proyecto: str) -> None:
    nombre_proyecto = validate_name(nombre_proyecto, "nombre")
    session_name = f"host_{nombre_proyecto}"

    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        stderr=subprocess.DEVNULL,
        check=False,
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE proyectos SET estado = 'inactivo', updated_at = NOW() WHERE nombre = %s",
                (nombre_proyecto,),
            )
        conn.commit()
        print(f"Proyecto '{nombre_proyecto}' marcado como inactivo y sesión tmux detenida.")
    finally:
        conn.close()


def usage() -> None:
    print(
        """Uso:
  python3 host_manager.py deploy <nombre> <subdominio> <comando_inicio>
  python3 host_manager.py list
  python3 host_manager.py stop <nombre>

Variables de entorno:
  DATABASE_URL, WORKSPACE_ROOT, PORT_RANGE_START, PORT_RANGE_END
"""
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
        sys.exit(1)

    cmd = sys.argv[1].lower()
    try:
        if cmd == "deploy" and len(sys.argv) >= 5:
            desplegar_proyecto(sys.argv[2], sys.argv[3], " ".join(sys.argv[4:]))
        elif cmd == "list":
            listar_proyectos()
        elif cmd == "stop" and len(sys.argv) >= 3:
            detener_proyecto(sys.argv[2])
        else:
            usage()
            sys.exit(1)
    except Exception as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        sys.exit(1)
