import os
import sys
import socket
import subprocess
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def get_free_port(start_port=3001):
    port = start_port
    while port < 9000:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
            port += 1
    raise Exception("No hay puertos disponibles en el rango especificado.")

def desplegar_proyecto(nombre_proyecto, subdominio, comando_inicio):
    puerto = get_free_port()
    path_proyecto = f"/workspace/proyectos/{nombre_proyecto}"
    
    os.makedirs(path_proyecto, exist_ok=True)

    # Registrar en la base de datos pasedata
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS proyectos (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(100) UNIQUE NOT NULL,
            subdominio VARCHAR(150) UNIQUE NOT NULL,
            puerto INT NOT NULL,
            comando VARCHAR(255) NOT NULL,
            estado VARCHAR(20) DEFAULT 'activo',
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        INSERT INTO proyectos (nombre, subdominio, puerto, comando, estado)
        VALUES (%s, %s, %s, %s, 'activo')
        ON CONFLICT (subdominio) 
        DO UPDATE SET puerto = EXCLUDED.puerto, comando = EXCLUDED.comando, estado = 'activo';
    """, (nombre_proyecto, subdominio, puerto, comando_inicio))
    conn.commit()
    cur.close()
    conn.close()

    # Iniciar el proceso en tmux pasando la variable PORT
    session_name = f"host_{nombre_proyecto}"
    exec_cmd = f"cd {path_proyecto} && PORT={puerto} {comando_inicio}"
    
    subprocess.run(["tmux", "kill-session", "-t", session_name], stderr=subprocess.DEVNULL)
    subprocess.run(["tmux", "new-session", "-d", "-s", session_name, f"bash -c '{exec_cmd}'"])

    print(f"✅ Proyecto '{nombre_proyecto}' alojado con éxito en pasedata.")
    print(f"   - Subdominio: {subdominio}")
    print(f"   - Puerto interno: {puerto}")
    print(f"   - Tmux Session: {session_name}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Uso: python3 host_manager.py <nombre> <subdominio> <comando_inicio>")
        sys.exit(1)
    
    desplegar_proyecto(sys.argv[1], sys.argv[2], sys.argv[3])
