"""
NOCTURNA OS - Deployment & Proxy Engine
Optimized for maximum web compatibility
"""

import os
import sys
import json
import time
import random
import logging
import subprocess
import threading
import requests  # Keep for non-proxy requests
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename

# Import curl_cffi for proxy requests (better browser impersonation)
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info("✅ curl_cffi disponible - Usando impersonación de navegador")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    curl_requests = requests  # Fallback to regular requests
    logger = logging.getLogger(__name__)
    logger.warning("⚠️ curl_cffi no disponible - Usando requests estándar")

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
        
        # Validar URL de GitHub
        if not any(domain in repo.lower() for domain in ['github.com', 'gitlab.com', 'bitbucket.org']):
            return jsonify({'status': 'error', 'msg': 'URL debe ser de GitHub, GitLab o Bitbucket'}), 400
        
        # Crear carpeta temporal
        temp_name = f"temp_{int(time.time())}"
        temp_path = Config.DEPLOY_DIR / temp_name
        
        logger.info(f"📦 Clonando: {repo} -> {temp_path}")
        
        # Limpiar si existe
        if temp_path.exists():
            subprocess.run(f"rm -rf {temp_path}", shell=True, check=True)
        
        # Verificar que git está instalado
        git_check = subprocess.run('git --version', shell=True, capture_output=True, text=True)
        if git_check.returncode != 0:
            return jsonify({
                'status': 'error', 
                'msg': 'Git no está instalado en el servidor'
            }), 500
        
        logger.info(f"✅ Git disponible: {git_check.stdout.strip()}")
        
        # Clonar repositorio con más detalles
        clone_cmd = f"git clone --depth 1 --progress {repo} {temp_path}"
        logger.info(f"🔄 Ejecutando: {clone_cmd}")
        
        result = subprocess.run(
            clone_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=Config.DEPLOY_TIMEOUT
        )
        
        # Log detallado del resultado
        logger.info(f"📊 Return code: {result.returncode}")
        if result.stdout:
            logger.info(f"📤 STDOUT: {result.stdout}")
        if result.stderr:
            logger.info(f"📥 STDERR: {result.stderr}")
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or 'Error desconocido'
            logger.error(f"❌ Error clonando: {error_msg}")
            
            # Mensajes de error específicos
            if 'not found' in error_msg.lower():
                return jsonify({
                    'status': 'error', 
                    'msg': 'Repositorio no encontrado. Verifica que la URL sea correcta y el repo sea público.'
                }), 404
            elif 'authentication' in error_msg.lower() or 'permission' in error_msg.lower():
                return jsonify({
                    'status': 'error',
                    'msg': 'Repositorio privado o sin permisos. Asegúrate de que sea público.'
                }), 403
            elif 'timeout' in error_msg.lower():
                return jsonify({
                    'status': 'error',
                    'msg': 'Timeout al clonar. El repositorio es muy grande o la conexión es lenta.'
                }), 504
            else:
                return jsonify({
                    'status': 'error', 
                    'msg': f'Error al clonar: {error_msg[:200]}'
                }), 500
        
        # Verificar que el directorio fue creado
        if not temp_path.exists():
            return jsonify({
                'status': 'error',
                'msg': 'El repositorio se clonó pero no se creó el directorio'
            }), 500
        
        # Listar archivos clonados
        try:
            files = os.listdir(temp_path)
            logger.info(f"📁 Archivos clonados: {files}")
        except Exception as e:
            logger.error(f"Error listando archivos: {e}")
        
        # Detectar tecnología
        install_cmd, start_cmd, tech = detect_tech(str(temp_path))
        
        logger.info(f"🔍 Detectado: {tech}")
        logger.info(f"📦 Install: {install_cmd}")
        logger.info(f"▶️  Start: {start_cmd}")
        
        return jsonify({
            'status': 'ok',
            'folder': str(temp_path),
            'inst': install_cmd,
            'start': start_cmd,
            'tech': tech
        })
        
    except subprocess.TimeoutExpired:
        logger.error("⏱️ Timeout al clonar repositorio")
        return jsonify({
            'status': 'error', 
            'msg': 'Timeout al clonar. El repositorio es muy grande (máx 10 minutos)'
        }), 500
    except Exception as e:
        logger.error(f"💥 Error inesperado en detect_and_setup: {e}", exc_info=True)
        return jsonify({
            'status': 'error', 
            'msg': f'Error inesperado: {str(e)}'
        }), 500

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
    Proxy optimizado con curl_cffi para máxima compatibilidad
    Impersona Chrome 120 para evitar detección
    """
    url = request.args.get('url')
    
    if not url:
        return "❌ ERROR: Parámetro 'url' requerido", 400
    
    # Validación de seguridad
    if not url.startswith(('http://', 'https://')):
        return "❌ ERROR: URL debe comenzar con http:// o https://", 400
    
    # Headers optimizados para pasar como navegador real
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
        'DNT': '1',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    # Añadir referer si viene de Google (ayuda con anti-bot)
    if 'google.com' in request.headers.get('Referer', ''):
        headers['Referer'] = 'https://www.google.com/'
    
    try:
        logger.info(f"🌐 Proxying: {url}")
        
        # Anti-detección: pequeño delay aleatorio para parecer humano
        time.sleep(random.uniform(0.1, 0.5))
        
        # Usar curl_cffi si está disponible (impersona Chrome 120)
        if CURL_CFFI_AVAILABLE:
            logger.debug("🎭 Usando curl_cffi con impersonación Chrome 120")
            response = curl_requests.get(
                url,
                headers=headers,
                timeout=Config.PROXY_TIMEOUT,
                allow_redirects=True,
                verify=True,
                impersonate="chrome120"  # 🎭 MAGIA: Impersona Chrome real
            )
        else:
            # Fallback a requests normal
            logger.debug("📡 Usando requests estándar")
            headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            response = requests.get(
                url,
                headers=headers,
                timeout=Config.PROXY_TIMEOUT,
                allow_redirects=True,
                verify=True
            )
        
        logger.info(f"✅ Respuesta: {response.status_code} - {len(response.content)} bytes")
        
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
        
        # Obtener contenido (ya decodificado por requests/curl_cffi)
        content = response.content
        content_type = response.headers.get('content-type', '').lower()
        
        # Si es HTML, inyectar base tag para recursos relativos
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
                elif '<html>' in html.lower() and '<head>' not in html.lower():
                    # Si no hay head, crear uno
                    html = html.replace('<html>', f'<html><head><base href="{base_url}"></head>', 1)
                
                content = html.encode('utf-8')
                logger.debug("📝 Base tag inyectado")
                
            except Exception as e:
                logger.warning(f"⚠️ No se pudo procesar HTML: {e}")
        
        return Response(
            content,
            status=response.status_code,
            headers=clean_headers
        )
        
    except requests.Timeout:
        logger.warning(f"Timeout: {url}")
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, sans-serif;
                    background: #0a0a0f;
                    color: #e8e8f2;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .error-container {{
                    text-align: center;
                    max-width: 600px;
                    padding: 40px;
                }}
                .error-icon {{ font-size: 64px; margin-bottom: 20px; }}
                h2 {{ color: #00fff5; margin-bottom: 10px; }}
                p {{ color: #9999b3; line-height: 1.6; margin: 15px 0; }}
                .btn {{
                    display: inline-block;
                    padding: 12px 24px;
                    background: linear-gradient(135deg, #00fff5, #0084ff);
                    color: #0a0a0f;
                    text-decoration: none;
                    border-radius: 8px;
                    margin: 10px 5px;
                    font-weight: 600;
                }}
                .btn:hover {{ opacity: 0.9; }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">⏱️</div>
                <h2>Tiempo de Espera Agotado</h2>
                <p>El sitio tardó demasiado en responder. Esto puede deberse a:</p>
                <ul style="text-align: left; color: #9999b3;">
                    <li>El sitio está experimentando alta carga</li>
                    <li>Conexión lenta o intermitente</li>
                    <li>El servidor está temporalmente caído</li>
                </ul>
                <p style="margin-top: 30px;">
                    <a href="{url}" target="_blank" class="btn">🌐 Abrir Directamente</a>
                    <a href="javascript:location.reload()" class="btn" style="background: #1a1a26;">🔄 Reintentar</a>
                </p>
            </div>
        </body>
        </html>
        """, 504
        
    except requests.TooManyRedirects:
    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, sans-serif;
                    background: #0a0a0f;
                    color: #e8e8f2;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .error-container {{
                    text-align: center;
                    max-width: 600px;
                    padding: 40px;
                }}
                .error-icon {{ font-size: 64px; margin-bottom: 20px; }}
                h2 {{ color: #00fff5; margin-bottom: 10px; }}
                p {{ color: #9999b3; line-height: 1.6; }}
                .btn {{
                    display: inline-block;
                    padding: 12px 24px;
                    background: linear-gradient(135deg, #00fff5, #0084ff);
                    color: #0a0a0f;
                    text-decoration: none;
                    border-radius: 8px;
                    margin-top: 20px;
                    font-weight: 600;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">🔄</div>
                <h2>Demasiadas Redirecciones</h2>
                <p>El sitio tiene un bucle de redirecciones. Intenta acceder directamente:</p>
                <a href="{url}" target="_blank" class="btn">🌐 Abrir en Nueva Pestaña</a>
            </div>
        </body>
        </html>
        """, 508
        
    except requests.SSLError:
        logger.warning(f"SSL Error: {url}")
return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, sans-serif;
                    background: #0a0a0f;
                    color: #e8e8f2;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .error-container {{
                    text-align: center;
                    max-width: 600px;
                    padding: 40px;
                }}
                .error-icon {{ font-size: 64px; margin-bottom: 20px; }}
                h2 {{ color: #ff006e; margin-bottom: 10px; }}
                p {{ color: #9999b3; line-height: 1.6; }}
                .btn {{
                    display: inline-block;
                    padding: 12px 24px;
                    background: linear-gradient(135deg, #00fff5, #0084ff);
                    color: #0a0a0f;
                    text-decoration: none;
                    border-radius: 8px;
                    margin-top: 20px;
                    font-weight: 600;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">🔒</div>
                <h2>Error de Certificado SSL</h2>
                <p>El sitio tiene un certificado SSL inválido o no confiable.</p>
                <p>Por seguridad, no se puede cargar a través del proxy.</p>
                <a href="{url}" target="_blank" class="btn">🌐 Abrir Directamente (bajo tu responsabilidad)</a>
            </div>
            </body>
        </html>
        """, 526
        
    except requests.ConnectionError:
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, sans-serif;
                    background: #0a0a0f;
                    color: #e8e8f2;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .error-container {{
                    text-align: center;
                    max-width: 600px;
                    padding: 40px;
                }}
                .error-icon {{ font-size: 64px; margin-bottom: 20px; }}
                h2 {{ color: #ff006e; margin-bottom: 10px; }}
                p {{ color: #9999b3; line-height: 1.6; }}
                .btn {{
                    display: inline-block;
                    padding: 12px 24px;
                    background: linear-gradient(135deg, #00fff5, #0084ff);
                    color: #0a0a0f;
                    text-decoration: none;
                    border-radius: 8px;
                    margin-top: 20px;
                    font-weight: 600;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">🔌</div>
                <h2>Error de Conexión</h2>
                <p>No se pudo conectar al sitio. Verifica que:</p>
                <ul style="text-align: left; color: #9999b3;">
                    <li>La URL sea correcta</li>
                    <li>El sitio esté activo</li>
                    <li>Tu conexión a internet funcione</li>
                </ul>
                <a href="{url}" target="_blank" class="btn">🌐 Intentar Abrir Directamente</a>
            </div>
        </body>
        </html>
        """, 503
        
    except Exception as e:
        logger.error(f"Error en proxy: {e}")
        
        # Detectar si es un error de X-Frame-Options o CSP
        error_msg = str(e).lower()
        is_frame_blocked = 'x-frame-options' in error_msg or 'frame-ancestors' in error_msg
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                    background: #0a0a0f;
            
color: #e8e8f2;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                }}
                .error-container {{
                    text-align: center;
                    max-width: 700px;
                    background: rgba(26, 26, 38, 0.7);
                    padding: 50px 40px;
                    border-radius: 16px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    backdrop-filter: blur(20px);
                }}
                .error-icon {{ 
                    font-size: 80px; 
                    margin-bottom: 24px;
                    animation: pulse 2s ease-in-out infinite;
                }}
                @keyframes pulse {{
                    0%, 100% {{ transform: scale(1); }}
                    50% {{ transform: scale(1.1); }}
                }}
                h2 {{ 
                    color: #00fff5; 
                    margin-bottom: 16px;
                    font-size: 28px;
                    font-weight: 700;
                }}
                .site-name {{
                    color: #b066ff;
                    font-weight: 600;
                    word-break: break-all;
                }}
                p {{ 
                    color: #9999b3; 
                    line-height: 1.7;
                    margin: 15px 0;
                    font-size: 15px;
                }}
                .info-box {{
                    background: rgba(0, 132, 255, 0.1);
                    border: 1px solid rgba(0, 132, 255, 0.3);
                    border-radius: 12px;
                    padding: 20px;
                    margin: 25px 0;
                    text-align: left;
                }}
                .info-box h3 {{
                    color: #0084ff;
                    margin: 0 0 12px 0;
                    font-size: 16px;
                }}
                .info-box ul {{
                    margin: 10px 0;
                    padding-left: 20px;
                    color: #9999b3;
                }}
                .info-box li {{
                    margin: 8px 0;
                }}
                .btn-group {{
                    display: flex;
                    gap: 12px;
                    justify-content: center;
                    flex-wrap: wrap;
                    margin-top: 30px;
                }}
                .btn {{
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
        padding: 14px 28px;
                    background: linear-gradient(135deg, #00fff5, #0084ff);
                    color: #0a0a0f;
                    text-decoration: none;
                    border-radius: 10px;
                    font-weight: 700;
                    font-size: 15px;
                    transition: all 0.3s;
                    border: none;
                    cursor: pointer;
                }}
                .btn:hover {{
                    transform: translateY(-2px);
                    box-shadow: 0 8px 24px rgba(0, 255, 245, 0.4);
                }}
                .btn-secondary {{
                    background: rgba(26, 26, 38, 0.9);
                    color: #e8e8f2;
                    border: 1.5px solid rgba(255, 255, 255, 0.1);
                }}
                .btn-secondary:hover {{
                    border-color: #00fff5;
                    box-shadow: 0 4px 16px rgba(0, 255, 245, 0.2);
                }}
                .error-details {{
                    margin-top: 20px;
                    padding: 15px;
                    background: rgba(255, 0, 110, 0.1);
                    border: 1px solid rgba(255, 0, 110, 0.3);
                    border-radius: 8px;
                    font-family: monospace;
                    font-size: 12px;
                    color: #ff006e;
                    text-align: left;
                    overflow-x: auto;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">🚫</div>
                <h2>Este sitio no puede cargarse en Nocturna</h2>
                <p>
                    <span class="site-name">{url}</span> 
                    {'bloquea activamente ser cargado en iframes' if is_frame_blocked else 'no se pudo cargar'} 
                    por razones de seguridad.
                </p>
                
                <div class="info-box">
                    <h3>💡 ¿Por qué sucede esto?</h3>
                    <ul>
                        <li><strong>Protección anti-frame:</strong> Sitios como Replit, Facebook, Instagram, y bancos bloquean ser mostrados dentro de otros sitios para prevenir ataques de clickjacking.</li>
                        <li><strong>Content Security Policy (CSP):</strong> Política de seguridad que restringe dónde puede cargarse el sitio.</li>
                        <li><strong>X-Frame-Options:</strong> Header HTTP que previene el uso de iframes.</li>
                    </ul>
                </div>

                <div class="info-box" style="background: rgba(0, 255, 148, 0.1); border-color: rgba(0, 255, 148, 0.3);">
                    <h3 style="color: #00ff94;">✅ Opciones alternativas:</h3>
                    <ul>
                        <li><strong>Abrir en nueva pestaña:</strong> El método más confiable</li>
                        <li><strong>Usar el sitio directamente:</strong> Sin restricciones de iframe</li>
                        <li><strong>Algunos sitios funcionan:</strong> Prueba Wikipedia, GitHub, Stack Overflow</li>
                    </ul>
                </div>

                <div class="btn-group">
                    <a href="{url}" target="_blank" class="btn">
                        🌐 Abrir en Nueva Pestaña
                    </a>
                    <a href="javascript:window.parent.goHome()" class="btn btn-secondary">
                        🏠 Volver al Inicio
                    </a>
                </div>

                <div class="error-details">
                    <strong>Error técnico:</strong> {str(e)[:200]}
                </div>
            </div>
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
