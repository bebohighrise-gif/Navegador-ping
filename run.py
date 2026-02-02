import os
import json
import threading
import time
import requests
from flask import Flask, render_template, request, redirect, Response, stream_with_context

app = Flask(__name__)

HISTORIAL_FILE = "historial.json"
PING_RESULT = ""

# --- PERSISTENCIA ---
def cargar_historial():
    if os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, "r") as f:
            return json.load(f)
    return ["https://www.google.com"]

urls_abiertas = cargar_historial()

def guardar_historial():
    with open(HISTORIAL_FILE, "w") as f:
        json.dump(urls_abiertas, f)

# --- MOTOR DE NAVEGACIÓN (PROXY) ---
@app.route('/proxy')
def proxy():
    """Motor que procesa la web para evitar el rechazo 'X-Frame-Options'"""
    url = request.args.get('url')
    if not url: return "URL vacía"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    
    try:
        # Hacemos la petición fingiendo ser un humano
        res = requests.get(url, headers=headers, stream=True, timeout=10, allow_redirects=True)
        
        # Eliminamos las cabeceras de seguridad que impiden que la web se vea en tu navegador
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection', 'x-frame-options', 'content-security-policy']
        headers_filtros = [(name, value) for (name, value) in res.raw.headers.items()
                           if name.lower() not in excluded_headers]

        return Response(res.content, res.status_code, headers_filtros)
    except Exception as e:
        return f"Error de conexión: {str(e)}"

# --- SISTEMA DE PING ---
@app.route("/ping_manual", methods=["POST"])
def ping_manual():
    global PING_RESULT
    target = request.form.get("ping_url")
    if target:
        if not target.startswith("http"): target = "https://" + target
        try:
            start = time.time()
            r = requests.get(target, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            ms = round((time.time() - start) * 1000)
            PING_RESULT = f"🟢 {target} | {r.status_code} | {ms}ms"
        except:
            PING_RESULT = f"🔴 Error al conectar con {target}"
    return redirect("/")

# --- RUTAS PRINCIPALES ---
@app.route("/", methods=["GET", "POST"])
def index():
    global urls_abiertas
    if request.method == "POST":
        nueva_url = request.form.get("url")
        if nueva_url:
            if not nueva_url.startswith("http"): nueva_url = "https://" + nueva_url
            if nueva_url not in urls_abiertas:
                urls_abiertas.append(nueva_url)
                guardar_historial()
        return redirect("/")
    
    return render_template("index.html", urls=urls_abiertas, ping_result=PING_RESULT)

@app.route("/cerrar/<int:idx>")
def cerrar(idx):
    if 0 <= idx < len(urls_abiertas):
        urls_abiertas.pop(idx)
        guardar_historial()
    return redirect("/")

if __name__ == "__main__":
    # Auto-ping para mantener el servidor despierto (Keep-Alive)
    threading.Thread(target=lambda: [time.sleep(60) or requests.get("http://localhost:5000") for _ in iter(int, 1)], daemon=True).start()
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))  
