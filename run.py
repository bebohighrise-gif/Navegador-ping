import os, requests, subprocess, threading, time, json, sys
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)

# --- MOTOR DE DETECCIÓN UNIVERSAL ---
def engine_universal(path):
    archivos = os.listdir(path)
    # 1. Soporte Python (Bots, APIs)
    if 'requirements.txt' in archivos or any(f.endswith('.py') for f in archivos):
        if 'requirements.txt' in archivos:
            subprocess.run(f"pip install --no-cache-dir -r {path}/requirements.txt", shell=True)
        for inicio in ['main.py', 'app.py', 'bot.py', 'index.py']:
            if inicio in archivos: return f"python {path}/{inicio}"
    # 2. Soporte Node.js (Apps web, JS bots)
    if 'package.json' in archivos:
        subprocess.run(f"cd {path} && npm install", shell=True)
        return f"cd {path} && npm start"
    return "ls -la"

@app.route('/deploy', methods=['POST'])
def deploy():
    data = request.json
    repo, name = data.get('repo'), data.get('name', 'instancia_pro')
    try:
        if os.path.exists(name): subprocess.run(f"rm -rf {name}", shell=True)
        subprocess.check_output(f"git clone {repo} {name}", shell=True)
        cmd_base = engine_universal(name)
        # BUCLE 24/7: Si el proceso muere, revive solo
        cmd_persistente = f"while true; do {cmd_base}; sleep 10; done"
        subprocess.Popen(f"nohup {cmd_persistente} > bot.log 2>&1 &", shell=True)
        return jsonify({"status": "ok", "msg": f"Lanzado con: {cmd_base}"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route('/proxy')
def proxy():
    url = request.args.get('url')
    # User-Agent de Chrome para evitar rechazos
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        res = requests.get(url, headers=headers, stream=True, timeout=15)
        # Filtramos cabeceras de seguridad que bloquean el uso de iframes
        excluded = ['x-frame-options', 'content-security-policy', 'content-encoding', 'strict-transport-security']
        h = [(k, v) for (k, v) in res.raw.headers.items() if k.lower() not in excluded]
        return Response(res.content, res.status_code, h)
    except:
        return "ERROR: No se pudo cargar la web. Verifica la URL."

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
