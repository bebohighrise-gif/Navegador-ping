from flask import Flask, render_template, request
import json
import os
import threading
import time
import requests

app = Flask(__name__)

HISTORIAL_FILE = "historial.json"
PING_RESULT = ""  # Resultado del ping manual

# Cargar historial de URLs
if os.path.exists(HISTORIAL_FILE):
    with open(HISTORIAL_FILE, "r") as f:
        urls_abiertas = json.load(f)
else:
    urls_abiertas = [
        "https://replit.com",
        "https://google.com"
    ]

def guardar_historial():
    with open(HISTORIAL_FILE, "w") as f:
        json.dump(urls_abiertas, f)

# Ping automático al servidor cada 1 minuto para mantenerlo despierto
def keep_alive():
    def ping_local():
        while True:
            try:
                requests.get("http://localhost:5000")
            except:
                pass
            time.sleep(60)  # ping cada 1 minuto
    threading.Thread(target=ping_local, daemon=True).start()

keep_alive()

@app.route("/", methods=["GET", "POST"])
def index():
    global urls_abiertas, PING_RESULT
    if request.method == "POST":
        url = request.form.get("url")
        ping_url = request.form.get("ping_url")
        
        # Abrir nueva web en el navegador
        if url:
            if not url.startswith("http"):
                url = "https://" + url
            if url not in urls_abiertas:
                urls_abiertas.append(url)
                guardar_historial()
        
        # Ping a cualquier URL desde el servidor
        if ping_url:
            if not ping_url.startswith("http"):
                ping_url = "https://" + ping_url
            try:
                r = requests.get(ping_url, timeout=5)
                PING_RESULT = f"Ping exitoso a {ping_url} → Código: {r.status_code}"
            except Exception as e:
                PING_RESULT = f"Error al hacer ping a {ping_url} → {e}"
    
    return render_template("index.html", urls=urls_abiertas, ping_result=PING_RESULT)

@app.route("/cerrar/<int:idx>")
def cerrar(idx):
    global urls_abiertas
    if 0 <= idx < len(urls_abiertas):
        urls_abiertas.pop(idx)
        guardar_historial()
    return render_template("index.html", urls=urls_abiertas, ping_result="")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
