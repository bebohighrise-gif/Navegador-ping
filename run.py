"""
NOCTURNA OS - Deployment & Proxy Engine
Optimized for maximum web compatibility
"""

import os
import sys
import json
import time
import logging
import subprocess
import threading
import requests
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename

# ===== CONFIGURACIÓN =====
class Config:
    PORT = int(os.environ.get("PORT", 5000))
    HOST = "0.0.0.0"
    DEBUG = False
    MAX_REPO_SIZE = 500 * 1024 * 1024  # 500MB
    DEPLOY_TIMEOUT = 600
    PROXY_TIMEOUT = 20
    LOG_DIR = Path("logs")
    DEPLOY_DIR = Path("deployments")
    
    # Crear directorios si no existen
    LOG_DIR.mkdir(exist_ok=True)
    DEPLOY_DIR.mkdir(exist_ok=True)

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Config.LOG_DIR / 'nocturna.log')
    ]
)
logger = logging.getLogger(__name__)

# ===== FLASK APP =====
app = Flask(__name__, static_folder='static', template_folder='.')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_REPO_SIZE

# ===== REGISTRO DE DEPLOYMENTS =====
deployments_registry = {}
deployments_lock = threading.Lock()

# ===== DETECCIÓN DE TECNOLOGÍA =====
def detect_tech(path):
    """Detecta el tipo de proyecto y retorna comandos"""
    try:
        files = set(os.listdir(path))
        logger.info(f"Archivos detectados: {files}")
        
        # Python
        if 'requirements.txt' in files or any(f.endswith('.py') for f in files):
            install = f"pip install --no-cache-dir -r {path}/requirements.txt" if 'requirements.txt' in files else ""
            
            # Buscar punto de entrada
            entry_points = ['main.py', 'app.py', 'bot.py', 'run.py', 'server.py', 'manage.py']
            for entry in entry_points:
                if entry in files:
                    if entry == 'manage.py':
                        return install, f"cd {path} && python manage.py runserver 0.0.0.0:8000", "Python/Django"
                    return install, f"python {path}/{entry}", "Python"
            
            return install, f"python {path}/main.py", "Python"
        
        # Node.js
        if 'package.json' in files:
            install = f"cd {path} && npm install"
            
            try:
                with open(Path(path) / 'package.json') as f:
                    pkg = json.load(f)
                    scripts = pkg.get('scripts', {})
                    
                    if 'dev' in scripts:
                        return install, f"cd {path} && npm run dev", "Node.js"
                    elif 'start' in scripts:
                        return install, f"cd {path} && npm start", "Node.js"
            except:
                pass
            
            return install, f"cd {path} && npm start", "Node.js"
        
        # Go
        if 'go.mod' in files:
            return f"cd {path} && go mod download", f"cd {path} && go run .", "Go"
        
        # PHP
        if 'composer.json' in files or any(f.endswith('.php') for f in files):
            install = f"cd {path} && composer install" if 'composer.json' in files else ""
            return install, f"cd {path} && php -S 0.0.0.0:8000", "PHP"
        
        # Ruby
        if 'Gemfile' in files:
            return f"cd {path} && bundle install", f"cd {path} && rails server -b 0.0.0.0", "Ruby/Rails"
        
        # Static
        if 'index.html' in files:
            return "", f"cd {path} && python3 -m http.server 8000", "HTML/Static"
        
        return "", "ls -la", "Unknown"
        
    except Exception as e:
        logger.error(f"Error en detect_tech: {e}")
        return "", "ls -la", "Unknown"

# ===== ROUTES =====

@app.route('/')
def index():
    """Sirve el archivo HTML principal"""
    try:
        with open('nocturna-ultimate.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        # Fallback a index.html si existe
        try:
            with open('index.html', 'r', encoding='utf-8') as f:
                return f.read()
        except:
            return """
            <!DOCTYPE html>
            <html>
            <head><title>Nocturna OS</title></head>
            <body>
                <h1>Nocturna OS</h1>
                <p>Por favor, coloca el archivo nocturna-ultimate.html en el mismo directorio que run.py</p>
            </body>
            </html>
            """, 404

@app.route('/detect_and_setup', methods=['POST'])
def detect_and_setup():
    """Analiza un repositorio y detecta tecnología"""
    try:
        data = request.json
        repo = data.get('repo', '')
        
        if not repo:
            return jsonify({'status': 'error', 'msg': 'URL de repositorio requerida'}), 400
        
        # Validar URL
        if not repo.startswith(('http://', 'https://')):
            return jsonify({'status': 'error', 'msg': 'URL inválida'}), 400
        
        # Crear carpeta temporal
        temp_name = f"temp_{int(time.time())}"
        temp_path = Config.DEPLOY_DIR / temp_name
        
        logger.info(f"Clonando: {repo} -> {temp_path}")
        
        # Limpiar si existe
        if temp_path.exists():
            subprocess.run(f"rm -rf {temp_path}", shell=True, check=True)
        
        # Clonar repositorio
        result = subprocess.run(
            f"git clone --depth 1 {repo} {temp_path}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=Config.DEPLOY_TIMEOUT
        )
        
        if result.returncode != 0:
            logger.error(f"Error clonando: {result.stderr}")
            return jsonify({'status': 'error', 'msg': f'Error al clonar: {result.stderr}'}), 500
        
        # Detectar tecnología
        install_cmd, start_cmd, tech = detect_tech(str(temp_path))
        
        logger.info(f"Detectado: {tech}")
        logger.info(f"Install: {install_cmd}")
        logger.info(f"Start: {start_cmd}")
        
        return jsonify({
            'status': 'ok',
            'folder': str(temp_path),
            'inst': install_cmd,
            'start': start_cmd,
            'tech': tech
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'msg': 'Timeout al clonar repositorio'}), 500
    except Exception as e:
        logger.error(f"Error en detect_and_setup: {e}")
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/deploy', methods=['POST'])
def deploy():
    """Despliega un proyecto"""
    try:
        data = request.json
        folder = data.get('folder', '')
        name = secure_filename(data.get('name', f'app_{int(time.time())}'))
        install_cmd = data.get('inst', '')
        start_cmd = data.get('start', '')
        
        if not all([folder, name, start_cmd]):
            return jsonify({'status': 'error', 'msg': 'Faltan parámetros requeridos'}), 400
        
        folder_path = Path(folder)
        if not folder_path.exists():
            return jsonify({'status': 'error', 'msg': 'Carpeta no encontrada'}), 404
        
        # Renombrar a nombre final
        final_path = Config.DEPLOY_DIR / name
        if final_path.exists():
            subprocess.run(f"rm -rf {final_path}", shell=True)
        
        folder_path.rename(final_path)
        logger.info(f"Proyecto renombrado: {final_path}")
        
        # Instalar dependencias
        if install_cmd and not install_cmd.startswith('echo'):
            logger.info(f"Instalando dependencias: {install_cmd}")
            result = subprocess.run(
                install_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=Config.DEPLOY_TIMEOUT
            )
            
            if result.returncode != 0:
                logger.error(f"Error instalando: {result.stderr}")
                return jsonify({'status': 'error', 'msg': f'Error instalando dependencias: {result.stderr}'}), 500
        
        # Crear archivo de log
        log_file = Config.LOG_DIR / f"{name}.log"
        
        # Comando con auto-restart
        restart_cmd = f"while true; do {start_cmd}; echo '[NOCTURNA] Proceso terminado, reiniciando en 10s...'; sleep 10; done"
        
        # Iniciar servicio
        logger.info(f"Iniciando servicio: {name}")
        with open(log_file, 'w') as log_fp:
            process = subprocess.Popen(
                restart_cmd,
                shell=True,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setpgrp
            )
        
        # Registrar deployment
        with deployments_lock:
            deployments_registry[name] = {
                'path': str(final_path),
                'pid': process.pid,
                'start_cmd': start_cmd,
                'started_at': datetime.now().isoformat(),
                'status': 'running'
            }
        
        logger.info(f"✅ Deployment exitoso: {name} (PID: {process.pid})")
        
        return jsonify({
            'status': 'ok',
            'msg': f'Desplegado exitosamente: {name}',
            'name': name,
            'pid': process.pid
        })
        
    except Exception as e:
        logger.error(f"Error en deploy: {e}")
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/get_logs/<n>')
def get_logs(name):
    """Obtiene los logs de un deployment"""
    try:
        name = secure_filename(name)
        log_file = Config.LOG_DIR / f"{name}.log"
        
        if not log_file.exists():
            return jsonify({'text': '[NOCTURNA] Esperando logs...', 'alert': None})
        
        # Leer últimas 100 líneas
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            last_lines = lines[-100:] if len(lines) > 100 else lines
            text = ''.join(last_lines)
        
        # Detectar estado
        alert = None
        text_lower = text.lower()
        if any(word in text_lower for word in ['started', 'listening', 'running', 'serving']):
            alert = 'success'
        elif any(word in text_lower for word in ['error', 'failed', 'exception']):
            alert = 'error'
        
        return jsonify({'text': text, 'alert': alert})
        
    except Exception as e:
        logger.error(f"Error leyendo logs: {e}")
        return jsonify({'text': f'Error: {str(e)}', 'alert': 'error'})

@app.route('/deployments')
def list_deployments():
    """Lista todos los deployments activos"""
    with deployments_lock:
        return jsonify(dict(deployments_registry))

@app.route('/ping')
def ping_service():
    """Verifica disponibilidad de un servicio"""
    url = request.args.get('url')
    
    if not url:
        return jsonify({'ok': False, 'error': 'URL requerida'}), 400
    
    try:
        start = time.time()
        response = requests.get(
            url,
            timeout=5,
            headers={'User-Agent': 'Nocturna-Monitor/1.0'},
            allow_redirects=True
        )
        elapsed = (time.time() - start) * 1000
        
        return jsonify({
            'ok': response.status_code < 400,
            'status_code': response.status_code,
            'response_time': round(elapsed, 2)
        })
        
    except requests.Timeout:
        return jsonify({'ok': False, 'error': 'Timeout'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/proxy')
def proxy():
    """
    Proxy optimizado para máxima compatibilidad web
    """
    url = request.args.get('url')
    
    if not url:
        return "❌ ERROR: Parámetro 'url' requerido", 400
    
    # Validación de seguridad
    if not url.startswith(('http://', 'https://')):
        return "❌ ERROR: URL debe comenzar con http:// o https://", 400
    
    # Headers optimizados para pasar como navegador real
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    try:
        logger.debug(f"Proxying: {url}")
        
        # Realizar request con configuración optimizada
        response = requests.get(
            url,
            headers=headers,
            timeout=Config.PROXY_TIMEOUT,
            allow_redirects=True,
            stream=True,
            verify=True  # Verificar SSL
        )
        
        # Headers problemáticos que bloquean iframes
        blocked_headers = [
            'x-frame-options',
            'content-security-policy',
            'content-security-policy-report-only',
            'x-content-security-policy',
            'x-webkit-csp',
            'strict-transport-security',
            'x-xss-protection',
            'content-encoding',
            'transfer-encoding',
            'connection',
            'keep-alive'
        ]
        
        # Filtrar headers
        clean_headers = []
        for key, value in response.headers.items():
            if key.lower() not in blocked_headers:
                clean_headers.append((key, value))
        
        # Agregar headers custom para permitir iframe
        clean_headers.extend([
            ('X-Frame-Options', 'ALLOWALL'),
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
            ('Access-Control-Allow-Headers', '*'),
        ])
        
        # Si es HTML, inyectar base tag para recursos relativos
        content = response.content
        content_type = response.headers.get('content-type', '').lower()
        
        if 'text/html' in content_type:
            try:
                # Decodificar contenido
                html = content.decode('utf-8', errors='ignore')
                
                # Inyectar base tag si no existe
                from urllib.parse import urlparse
                parsed = urlparse(url)
                base_url = f"{parsed.scheme}://{parsed.netloc}"
                
                if '<head>' in html.lower() and '<base' not in html.lower():
                    html = html.replace('<head>', f'<head><base href="{base_url}">', 1)
                    content = html.encode('utf-8')
                
            except Exception as e:
                logger.warning(f"No se pudo inyectar base tag: {e}")
        
        return Response(
            content,
            status=response.status_code,
            headers=clean_headers
        )
        
    except requests.Timeout:
        logger.warning(f"Timeout: {url}")
        return """
        <html>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h2>⏱️ Timeout</h2>
            <p>El sitio tardó demasiado en responder</p>
            <p style="color: #666;">Intenta acceder directamente: <a href="{}">{}</a></p>
        </body>
        </html>
        """.format(url, url), 504
        
    except requests.TooManyRedirects:
        return """
        <html>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h2>🔄 Demasiadas redirecciones</h2>
            <p>El sitio tiene demasiadas redirecciones</p>
            <p style="color: #666;">Intenta acceder directamente: <a href="{}">{}</a></p>
        </body>
        </html>
        """.format(url, url), 508
        
    except requests.SSLError:
        logger.warning(f"SSL Error: {url}")
        return """
        <html>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h2>🔒 Error SSL</h2>
            <p>El sitio tiene un certificado SSL inválido</p>
            <p style="color: #666;">Intenta acceder directamente: <a href="{}">{}</a></p>
        </body>
        </html>
        """.format(url, url), 526
        
    except requests.ConnectionError:
        return """
        <html>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h2>🔌 Error de Conexión</h2>
            <p>No se pudo conectar al sitio</p>
            <p style="color: #666;">Verifica que la URL sea correcta</p>
        </body>
        </html>
        """, 503
        
    except Exception as e:
        logger.error(f"Error en proxy: {e}")
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h2>❌ Error</h2>
            <p>No se pudo cargar la página</p>
            <p style="color: #666;">Error: {str(e)}</p>
            <p style="color: #666;">URL: <a href="{url}">{url}</a></p>
        </body>
        </html>
        """, 500

@app.route('/health')
def health():
    """Health check"""
    with deployments_lock:
        count = len(deployments_registry)
    
    return jsonify({
        'status': 'healthy',
        'deployments': count,
        'timestamp': datetime.now().isoformat()
    })

# ===== ERROR HANDLERS =====

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

# ===== MAIN =====

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 NOCTURNA OS - Deployment & Proxy Engine")
    print("=" * 60)
    print(f"🌐 Host: {Config.HOST}:{Config.PORT}")
    print(f"📁 Deployments: {Config.DEPLOY_DIR}")
    print(f"📝 Logs: {Config.LOG_DIR}")
    print("=" * 60)
    
    try:
        app.run(
            host=Config.HOST,
            port=Config.PORT,
            debug=Config.DEBUG,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\n👋 Apagando...")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)
