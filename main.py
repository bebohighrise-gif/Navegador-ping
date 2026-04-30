"""
NexHost v3 — Motor uv con aislamiento total por proyecto
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Estrategia de persistencia (capas, de más a menos prioritaria):
  1. /data/nexhost  → HF Spaces "Persistent Storage" (si está activado)
  2. ~/.nexhost     → HOME del usuario (sobrevive reinicios normales)
  3. /tmp/nexhost   → Último recurso (se pierde al reiniciar)

Motor uv (reemplaza Micromamba):
  - uv se instala UNA sola vez a PERSIST/bin/uv
  - Las dependencias se instalan con: uv pip install --system -r requirements.txt
  - Sin entornos virtuales adicionales — instala directo en el entorno del hosting
  - Zero conflictos de setuptools ni capas extra
"""
import os, json, subprocess, threading, time, shutil, sys, socket, hashlib, re, signal
import base64, zipfile, io as _io_mod
import urllib.request, urllib.error, urllib.parse as _urllib_parse
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Configuración global ──────────────────────────────────────────────────
PORT       = int(os.environ.get("PORT", 7860))
_NX_PASS   = os.environ.get("NEXHOST_PASSWORD", "nexhost")
AUTH_TOKEN = hashlib.sha256(_NX_PASS.encode()).hexdigest()

# ── Cloudflare Turnstile ───────────────────────────────────────────────────
CF_SITE_KEY   = os.environ.get("CF_TURNSTILE_SITE_KEY", "").strip()
CF_SECRET_KEY = os.environ.get("CF_TURNSTILE_SECRET", "").strip()
# Si no hay keys configuradas, Turnstile se omite (modo degradado)
CF_TURNSTILE_ENABLED = bool(CF_SITE_KEY and CF_SECRET_KEY)

def _verify_turnstile(token: str, remote_ip: str = "") -> tuple[bool, str]:
    """Verifica un token Turnstile con la API de Cloudflare."""
    if not CF_TURNSTILE_ENABLED:
        return True, "disabled"
    if not token:
        return False, "missing_token"
    try:
        data = _urllib_parse.urlencode({
            "secret":   CF_SECRET_KEY,
            "response": token,
            "remoteip": remote_ip,
        }).encode()
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
        if result.get("success"):
            return True, "ok"
        codes = ",".join(result.get("error-codes", ["unknown"]))
        return False, codes
    except Exception as e:
        # Si Cloudflare no responde, permitir el login (fail-open)
        print(f"[nexhost] ⚠ Turnstile verificación fallida: {e} — permitiendo login")
        return True, "network_error"

# ── Rate limiting / Brute-force protection ────────────────────────────────
_MAX_ATTEMPTS  = int(os.environ.get("NX_MAX_LOGIN_ATTEMPTS", "5"))
_LOCKOUT_TIME  = int(os.environ.get("NX_LOCKOUT_SECONDS", "900"))  # 15 min
_ATTEMPT_WINDOW = 600   # ventana de 10 min para contar intentos
_ip_attempts: dict = {}  # ip → [timestamp, ...]
_ip_lock = threading.Lock()

def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Retorna (permitido, segundos_hasta_unlock).
    Limpia intentos antiguos automáticamente.
    """
    now = time.time()
    with _ip_lock:
        attempts = _ip_attempts.get(ip, [])
        # Limpiar intentos fuera de la ventana
        attempts = [t for t in attempts if now - t < _ATTEMPT_WINDOW]
        _ip_attempts[ip] = attempts
        if len(attempts) >= _MAX_ATTEMPTS:
            oldest = attempts[0]
            unlock_in = int(_LOCKOUT_TIME - (now - oldest))
            if unlock_in > 0:
                return False, unlock_in
            # Lockout expirado — limpiar
            _ip_attempts[ip] = []
            return True, 0
        return True, 0

def _record_attempt(ip: str):
    now = time.time()
    with _ip_lock:
        _ip_attempts.setdefault(ip, []).append(now)

def _clear_attempts(ip: str):
    with _ip_lock:
        _ip_attempts.pop(ip, None)

# ── Audit log de accesos ───────────────────────────────────────────────────
_AUDIT_LOG: list = []   # [{ts, ip, success, reason, ua}]
_AUDIT_LOCK = threading.Lock()
_AUDIT_MAX  = 500       # máximo de entradas en memoria

def _audit(ip: str, success: bool, reason: str = "", ua: str = ""):
    entry = {
        "ts":      datetime.now().isoformat(timespec="seconds"),
        "ip":      ip,
        "ok":      success,
        "reason":  reason,
        "ua":      ua[:120] if ua else "",
    }
    with _AUDIT_LOCK:
        _AUDIT_LOG.append(entry)
        if len(_AUDIT_LOG) > _AUDIT_MAX:
            del _AUDIT_LOG[:-_AUDIT_MAX]


_TOKEN_TTL = 7 * 24 * 3600  # 7 días
_active_tokens: dict = {}    # token → {"user_id": str, "expiry": float}
_tokens_lock = threading.Lock()

def _generate_session_token(user_id: str = "admin") -> str:
    """Genera un token de sesión único con TTL, asociado a un user_id."""
    raw = f"{AUTH_TOKEN}:{user_id}:{time.time()}:{os.urandom(16).hex()}"
    token = hashlib.sha256(raw.encode()).hexdigest()
    with _tokens_lock:
        _active_tokens[token] = {"user_id": user_id, "expiry": time.time() + _TOKEN_TTL}
    return token

def _token_expiry(rec) -> float:
    if isinstance(rec, dict):
        return rec.get("expiry", 0)
    if isinstance(rec, (int, float)):
        return rec
    return 0

def _validate_session_token(token: str) -> bool:
    """Valida un token de sesión. Renueva automáticamente si está a más de 50% del TTL."""
    if not token:
        return False
    # Compatibilidad: el AUTH_TOKEN legacy también es válido (admin implícito)
    if token == AUTH_TOKEN:
        return True
    with _tokens_lock:
        rec = _active_tokens.get(token)
        if rec is None:
            return False
        expiry = _token_expiry(rec)
        if time.time() > expiry:
            del _active_tokens[token]
            return False
        # Auto-renovar si queda menos del 50% del TTL
        remaining = expiry - time.time()
        if remaining < _TOKEN_TTL * 0.5:
            new_exp = time.time() + _TOKEN_TTL
            if isinstance(rec, dict):
                rec["expiry"] = new_exp
            else:
                _active_tokens[token] = new_exp
        return True

def _session_user_id(token: str) -> str:
    """Devuelve user_id para un token válido. Vacío si inválido. 'admin' para legacy."""
    if not token:
        return ""
    if token == AUTH_TOKEN:
        return "admin"
    with _tokens_lock:
        rec = _active_tokens.get(token)
        if rec is None:
            return ""
        if isinstance(rec, dict):
            return rec.get("user_id", "admin")
        return "admin"

def _revoke_session_token(token: str):
    """Invalida un token de sesión."""
    with _tokens_lock:
        _active_tokens.pop(token, None)

def _revoke_all_user_tokens(user_id: str):
    """Invalida todas las sesiones activas de un user_id."""
    with _tokens_lock:
        to_kill = [t for t, rec in _active_tokens.items()
                   if isinstance(rec, dict) and rec.get("user_id") == user_id]
        for t in to_kill:
            del _active_tokens[t]

def _cleanup_expired_tokens():
    """Limpia tokens expirados periódicamente."""
    while True:
        time.sleep(3600)
        now = time.time()
        with _tokens_lock:
            expired = [t for t, rec in _active_tokens.items() if now > _token_expiry(rec)]
            for t in expired:
                del _active_tokens[t]

threading.Thread(target=_cleanup_expired_tokens, daemon=True).start()

# Capas de persistencia (de más a menos prioritaria)
def _pick_persist():
    for candidate in [Path("/data/nexhost"), Path.home() / ".nexhost", Path("/tmp/nexhost")]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            (candidate / ".probe").touch(); (candidate / ".probe").unlink()
            return candidate
        except Exception:
            continue
    return Path("/tmp/nexhost")

PERSIST         = _pick_persist()
BASE_DIR        = PERSIST / "projects"
DATA_DIR        = PERSIST / "data"
LOGS_DIR        = PERSIST / "logs"
PROJECT_DATA_BASE = PERSIST / "pdata"
ENVS_BASE       = Path("/tmp/nx_envs")
BIN_DIR         = PERSIST / "bin"

for _d in (BASE_DIR, DATA_DIR, LOGS_DIR, PROJECT_DATA_BASE, ENVS_BASE, BIN_DIR):
    _d.mkdir(parents=True, exist_ok=True)

UV_BIN = BIN_DIR / "uv"

# ════════════════════════════════════════════════════════════════════════════
# ── SISTEMA DE USUARIOS · OAuth Google · SendGrid · Admin ──────────────────
# ════════════════════════════════════════════════════════════════════════════

USERS_FILE      = DATA_DIR / "users.json"
USER_DATA_BASE  = PERSIST / "usuarios"
USER_DATA_BASE.mkdir(parents=True, exist_ok=True)

_users_lock = threading.Lock()

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "").strip()
SENDGRID_FROM    = os.environ.get("SENDGRID_FROM", "noreply@nexhost.app").strip()
SENDGRID_ENABLED = bool(SENDGRID_API_KEY)

# Estado OAuth temporal: state → {created, redirect_uri, mode}
_oauth_states: dict = {}
_oauth_lock = threading.Lock()

def _hash_password(password: str, salt: str = "") -> str:
    """PBKDF2-HMAC-SHA256 con salt — salt:hex(hash)."""
    if not salt:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}:{dk.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    if not password or not stored or ":" not in stored:
        return False
    salt, _ = stored.split(":", 1)
    return _hash_password(password, salt) == stored

def load_users() -> list:
    """Carga la lista de usuarios desde disco."""
    if not USERS_FILE.exists():
        return []
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_users(users: list):
    with _users_lock:
        USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")

def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()

def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()

def get_user(user_id: str):
    if not user_id:
        return None
    for u in load_users():
        if u.get("id") == user_id:
            return u
    return None

def get_user_by_email(email: str):
    em = _normalize_email(email)
    if not em:
        return None
    for u in load_users():
        if _normalize_email(u.get("email", "")) == em:
            return u
    return None

def get_user_by_username(username: str):
    un = _normalize_username(username)
    if not un:
        return None
    for u in load_users():
        if _normalize_username(u.get("username", "")) == un:
            return u
    return None

def get_user_by_google_sub(sub: str):
    if not sub:
        return None
    for u in load_users():
        if u.get("google_sub") == sub:
            return u
    return None

def update_user(user_id: str, **fields):
    """Actualiza campos de un usuario y persiste."""
    users = load_users()
    changed = False
    for u in users:
        if u.get("id") == user_id:
            for k, v in fields.items():
                u[k] = v
            changed = True
            break
    if changed:
        save_users(users)
    return changed

def create_user(*, username: str = "", email: str = "", password: str = "",
                google_sub: str = "", role: str = "user", picture: str = "",
                name: str = "") -> tuple:
    """Crea un usuario nuevo. Retorna (user, error)."""
    username = (username or "").strip()
    email    = _normalize_email(email)
    if not username:
        return None, "Falta el nombre de usuario"
    if len(username) < 3 or len(username) > 32:
        return None, "El usuario debe tener entre 3 y 32 caracteres"
    if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
        return None, "Solo letras, números y . _ -"
    if email and "@" not in email:
        return None, "Email inválido"
    if not password and not google_sub:
        return None, "Falta contraseña"
    if password and len(password) < 6:
        return None, "Contraseña mínima de 6 caracteres"
    users = load_users()
    if any(_normalize_username(u.get("username", "")) == _normalize_username(username) for u in users):
        return None, "Ese usuario ya existe"
    if email and any(_normalize_email(u.get("email", "")) == email for u in users):
        return None, "Ese email ya está registrado"
    if google_sub and any(u.get("google_sub") == google_sub for u in users):
        return None, "Esa cuenta Google ya está vinculada"

    user_id = google_sub or ("u_" + hashlib.sha256(
        f"{username}:{email}:{time.time()}:{os.urandom(8).hex()}".encode()
    ).hexdigest()[:24])

    user = {
        "id":            user_id,
        "username":      username,
        "email":         email,
        "name":          name or username,
        "picture":       picture,
        "password_hash": _hash_password(password) if password else "",
        "google_sub":    google_sub,
        "role":          role,
        "gh_token":      "",
        "banned_until":  0,        # 0 = no baneado, -1 = baneo permanente, >now = temporal
        "ban_reason":    "",       # motivo del último baneo (opcional)
        "ban_by":        "",       # username del admin que aplicó el baneo
        "ban_at":        0,        # timestamp del baneo
        "created_at":    int(time.time()),
        "last_login":    0,
        "notify_errors": True,
    }
    users.append(user)
    save_users(users)
    # Crear carpeta de datos personal
    try:
        (USER_DATA_BASE / user_id).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return user, ""

def is_banned(user) -> tuple:
    """Retorna (banned, msg). Verifica ban temporal o permanente.
    El mensaje incluye el motivo si está registrado."""
    if not user:
        return False, ""
    bu = user.get("banned_until", 0)
    reason = (user.get("ban_reason") or "").strip()
    by     = (user.get("ban_by") or "").strip()
    suffix = ""
    if reason:
        suffix += f" Motivo: {reason}."
    if by:
        suffix += f" Aplicado por: @{by}."
    if bu == -1:
        return True, "Tu cuenta ha sido suspendida permanentemente." + suffix
    if bu and bu > time.time():
        rem = int(bu - time.time())
        h = rem // 3600
        m = (rem % 3600) // 60
        if h:
            t = f"{h}h {m}m"
        else:
            t = f"{m}m {rem % 60}s"
        return True, f"Cuenta suspendida temporalmente. Restante: {t}." + suffix
    return False, ""

def is_admin_user(user) -> bool:
    return bool(user and user.get("role") == "admin")

def bootstrap_admin():
    """Crea el usuario admin si no existe ningún admin todavía."""
    users = load_users()
    if any(u.get("role") == "admin" for u in users):
        return
    # Usar la contraseña original NEXHOST_PASSWORD
    admin_pass = os.environ.get("NEXHOST_PASSWORD", "nexhost")
    admin_email = os.environ.get("NEXHOST_ADMIN_EMAIL", "").strip()
    user, err = create_user(
        username="admin",
        email=admin_email,
        password=admin_pass,
        role="admin",
        name="Administrador",
    )
    if user:
        print(f"[nexhost] ✓ Usuario admin creado (login: admin / contraseña actual)")
    else:
        print(f"[nexhost] ⚠ No se pudo crear admin: {err}")

# Bootstrap inmediato (antes de aceptar requests)
bootstrap_admin()

# ── Carpeta de datos por usuario ───────────────────────────────────────────
def user_data_dir(user_id: str) -> Path:
    """Carpeta /usuarios/<id>/ en el almacenamiento persistente."""
    p = USER_DATA_BASE / (user_id or "anon")
    p.mkdir(parents=True, exist_ok=True)
    return p

def project_belongs_to(project: dict, user) -> bool:
    """True si el proyecto pertenece al usuario o si el usuario es admin."""
    if not project:
        return False
    if is_admin_user(user):
        return True
    if not user:
        return False
    owner = project.get("owner_id", "")
    # Proyectos sin owner = legacy (admin)
    if not owner:
        return user.get("role") == "admin"
    return owner == user.get("id")

# ── SendGrid (REST v3, urllib puro) ────────────────────────────────────────
def send_email(to_email: str, subject: str, html: str, text: str = "") -> bool:
    """Envía un email por SendGrid. Retorna True si se aceptó."""
    if not SENDGRID_ENABLED:
        return False
    if not to_email or "@" not in to_email:
        return False
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": SENDGRID_FROM, "name": "NexHost"},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text or re.sub(r'<[^>]+>', '', html)},
            {"type": "text/html",  "value": html},
        ],
    }
    try:
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"[nexhost] ⚠ SendGrid error: {e}")
        return False

def notify_project_error(project: dict, error_msg: str = "", returncode: int = -1):
    """Avisa al dueño del proyecto que falló."""
    if not SENDGRID_ENABLED:
        return
    owner_id = project.get("owner_id", "")
    if not owner_id:
        return
    owner = get_user(owner_id)
    if not owner or not owner.get("email") or not owner.get("notify_errors", True):
        return
    pid  = project.get("id", "")
    name = project.get("name", pid)
    repo = project.get("repo_url", "")
    safe_msg = (error_msg or "")[:1500].replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!DOCTYPE html><html><body style="font-family:system-ui,sans-serif;background:#0a0a14;color:#e8e8f0;padding:24px;margin:0">
<div style="max-width:560px;margin:0 auto;background:#10101c;border:1px solid #2a2a44;border-radius:14px;overflow:hidden">
  <div style="padding:18px 22px;background:#1a0a14;border-bottom:1px solid #4a1a2a">
    <h2 style="margin:0;color:#ff6680;font-size:18px">⚠ NexHost — Proyecto caído</h2>
  </div>
  <div style="padding:22px">
    <p style="color:#c0c0d8;margin:0 0 14px">Hola <strong>{owner.get('username','')}</strong>,</p>
    <p style="color:#c0c0d8;margin:0 0 14px">Tu proyecto <strong style="color:#00f5a0">{name}</strong> ha terminado con error.</p>
    <table style="width:100%;border-collapse:collapse;margin:14px 0;font-size:13px">
      <tr><td style="padding:6px 0;color:#80809a;width:120px">ID</td><td style="color:#e8e8f0;font-family:monospace">{pid}</td></tr>
      <tr><td style="padding:6px 0;color:#80809a">Código salida</td><td style="color:#ff8090;font-family:monospace">{returncode}</td></tr>
      {'<tr><td style="padding:6px 0;color:#80809a">Repo</td><td style="color:#80aaff;font-family:monospace;font-size:11px">' + repo + '</td></tr>' if repo else ''}
    </table>
    {'<pre style="background:#050510;padding:14px;border-radius:8px;color:#c0c0d8;font-size:11px;overflow:auto;max-height:240px;border:1px solid #2a2a44">' + safe_msg + '</pre>' if safe_msg else ''}
    <p style="color:#80809a;font-size:12px;margin:20px 0 0;line-height:1.6">Entra al panel para revisar logs y reiniciar el proyecto.</p>
  </div>
  <div style="padding:14px 22px;background:#0a0a14;border-top:1px solid #2a2a44;color:#50506a;font-size:11px;font-family:monospace">
    NexHost · notificación automática
  </div>
</div>
</body></html>"""
    threading.Thread(
        target=send_email,
        args=(owner["email"], f"[NexHost] {name} cayó (código {returncode})", html),
        daemon=True,
    ).start()

# ── Google OAuth helpers ───────────────────────────────────────────────────
GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO  = "https://openidconnect.googleapis.com/v1/userinfo"

def _oauth_redirect_uri(handler) -> str:
    """Calcula la URL absoluta del callback OAuth (debe estar registrada en Google Cloud)."""
    base = _canonical_base(handler)  # definido más adelante
    return f"{base.rstrip('/')}/api/google/callback"

def _new_oauth_state(redirect_to: str = "/") -> str:
    state = os.urandom(16).hex()
    with _oauth_lock:
        # Limpiar estados viejos (>10 min)
        now = time.time()
        for k in list(_oauth_states.keys()):
            if now - _oauth_states[k]["created"] > 600:
                del _oauth_states[k]
        _oauth_states[state] = {"created": now, "redirect_to": redirect_to}
    return state

def _consume_oauth_state(state: str) -> bool:
    with _oauth_lock:
        rec = _oauth_states.pop(state, None)
    if not rec:
        return False
    return time.time() - rec["created"] < 600

def google_exchange_code(code: str, redirect_uri: str) -> dict:
    """Intercambia code por access_token + id_token."""
    data = _urllib_parse.urlencode({
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def google_userinfo(access_token: str) -> dict:
    req = urllib.request.Request(
        GOOGLE_USERINFO,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

# ════════════════════════════════════════════════════════════════════════════

registry: dict = {}
registry_lock   = threading.Lock()

# ── Logs ──────────────────────────────────────────────────────────────────
def _log_file(pid, log_type="build"):
    suffix = f".{log_type}" if log_type else ""
    return LOGS_DIR / f"{pid}{suffix}.log"

def append_log(pid, msg, log_type="build"):
    lf = _log_file(pid, log_type)
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        with open(lf, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def get_logs(pid, lines=300, log_type="build"):
    lf = _log_file(pid, log_type)
    if not lf.exists():
        return []
    try:
        all_lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
        return all_lines[-lines:]
    except Exception:
        return []

def clear_log_file(pid, log_type="build"):
    lf = _log_file(pid, log_type)
    if lf.exists():
        lf.write_text("")

# ── Projects persistence ───────────────────────────────────────────────────
_PROJECTS_FILE = DATA_DIR / "projects.json"

# ── Backup de proyectos en HF Dataset (sobrevive reinicios sin /data) ──────
_HF_META_DATASET = os.environ.get("BOT_DATA_TOKEN", "") and os.environ.get("HF_META_DATASET", "")

def _hf_global_token() -> str:
    return os.environ.get("BOT_DATA_TOKEN", "") or os.environ.get("HF_TOKEN", "")

def _hf_meta_dataset() -> str:
    """ID del dataset usado para guardar el projects.json globalmente."""
    explicit = os.environ.get("HF_META_DATASET", "").strip()
    if explicit:
        return explicit
    # Derivar del SPACE_ID: "owner/space-name" → "owner/nexhost-meta"
    space_id = os.environ.get("SPACE_ID", "").strip()
    if space_id and "/" in space_id:
        owner = space_id.split("/")[0]
        return f"{owner}/nexhost-meta"
    return ""

def _backup_projects_to_hf(projs):
    """Sube projects.json al dataset HF como respaldo ante reinicios."""
    token = _hf_global_token()
    dataset_id = _hf_meta_dataset()
    if not token or not dataset_id:
        return
    try:
        api = _hf_api(token)
        if api is None:
            return
        data = json.dumps(projs, indent=2).encode()
        import io as _io
        api.upload_file(
            path_or_fileobj=_io.BytesIO(data),
            path_in_repo="projects.json",
            repo_id=dataset_id,
            repo_type="dataset",
            commit_message="nexhost: backup projects",
        )
    except Exception as e:
        # Silencioso — el backup es best-effort
        print(f"[nexhost] ⚠ Backup HF skipped: {e}")

def _restore_projects_from_hf() -> list:
    """Descarga projects.json desde HF Dataset si el archivo local no existe."""
    token = _hf_global_token()
    dataset_id = _hf_meta_dataset()
    if not token or not dataset_id:
        return []
    try:
        api = _hf_api(token)
        if api is None:
            return []
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=dataset_id,
            filename="projects.json",
            repo_type="dataset",
            token=token,
            local_dir=str(DATA_DIR),
        )
        raw = Path(path).read_text(encoding="utf-8")
        projs = json.loads(raw)
        # Copiar al destino local canónico
        _PROJECTS_FILE.write_text(json.dumps(projs, indent=2))
        print(f"[nexhost] ✓ projects.json restaurado desde HF Dataset ({len(projs)} proyectos)")
        return projs
    except Exception as e:
        print(f"[nexhost] ⚠ No se pudo restaurar projects.json desde HF: {e}")
        return []

def load_projects():
    if not _PROJECTS_FILE.exists():
        return []
    try:
        return json.loads(_PROJECTS_FILE.read_text())
    except Exception:
        return []

def save_projects(projs):
    _PROJECTS_FILE.write_text(json.dumps(projs, indent=2))
    # Backup asíncrono al dataset HF — no bloquea
    threading.Thread(target=_backup_projects_to_hf, args=(projs,), daemon=True).start()

def load_config():
    """Lee toda la config directamente de los secrets del HF Space."""
    return {
        "gh_token":  os.environ.get("GH_TOKEN", ""),
        "hf_token":  os.environ.get("BOT_DATA_TOKEN", ""),
    }

def save_config(cfg):
    """No-op: la config viene de secrets del Space, no se guarda en archivo."""
    pass  # Los secrets se gestionan desde HuggingFace Space settings

def _set_project_status(pid, status):
    projs = load_projects()
    for p in projs:
        if p["id"] == pid:
            p["status"] = status
    save_projects(projs)

def _set_project_field(pid, field, value):
    projs = load_projects()
    for p in projs:
        if p["id"] == pid:
            p[field] = value
    save_projects(projs)

# ── Helpers de red ─────────────────────────────────────────────────────────
def find_free_port(start=5000, end=5999):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start

def is_port_open(port, host="127.0.0.1", timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def stream_output(pid, proc):
    _ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
    def _read(stream):
        try:
            for raw in iter(stream.readline, b""):
                try:
                    line = raw.decode("utf-8", errors="replace")
                except Exception:
                    line = repr(raw)
                clean = _ANSI.sub("", line).rstrip()
                if clean:
                    append_log(pid, clean, "process")
        except Exception:
            pass
    t_out = threading.Thread(target=_read, args=(proc.stdout,), daemon=True)
    t_err = threading.Thread(target=_read, args=(proc.stderr,), daemon=True)
    t_out.start(); t_err.start()
    proc.wait()
    t_out.join(timeout=5); t_err.join(timeout=5)
    with registry_lock:
        info = registry.get(pid, {})
        if info.get("proc") is proc:
            registry[pid]["status"] = "stopped"
    _set_project_status(pid, "stopped")
    append_log(pid, f"[nexhost] Proceso terminado (código {proc.returncode})", "process")

    # ── Notificar al dueño por email si el proceso falló ──
    if proc.returncode not in (0, -signal.SIGTERM, -signal.SIGKILL, None):
        try:
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if proj:
                # Tomar últimas líneas del log de proceso como contexto
                last_lines = ""
                try:
                    lf = _log_file(pid, "process")
                    if lf.exists():
                        with lf.open("r", encoding="utf-8", errors="replace") as fh:
                            tail = fh.readlines()[-25:]
                            last_lines = "".join(tail)
                except Exception:
                    pass
                threading.Thread(
                    target=notify_project_error,
                    args=(proj, last_lines, proc.returncode),
                    daemon=True,
                ).start()
        except Exception as e:
            print(f"[notify] error: {e}")

# ── HuggingFace Dataset — urllib puro, sin dependencias externas ──────────
_hf_watchers: dict = {}          # pid -> {"thread": t, "stop": Event}
_hf_watcher_lock = threading.Lock()

# Semáforo global: máximo 3 subidas simultáneas a HF en todo el proceso
_hf_upload_sem = threading.Semaphore(3)

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".uv", "logs", "log"}
SKIP_EXTS = {".pyc", ".pyo", ".log", ".tmp", ".swp", ".lock", ".md"}
SKIP_FILES = {".gitignore", ".gitkeep", ".replit", "replit.nix", ".env.example",
              "runtime.txt", "render.yaml", "render.yaml.txt", "Readme", "README"}

def _should_skip_file(f: Path) -> bool:
    """True si el archivo debe omitirse al subir al dataset HF.
    Excluye cualquier archivo cuyo nombre contenga 'logs' o 'log.txt' (sin importar mayúsculas)."""
    if any(p in SKIP_DIRS for p in f.parts): return True
    if f.suffix.lower() in SKIP_EXTS: return True
    if f.name in SKIP_FILES: return True
    if "log" in f.name.lower(): return True
    return False


def _hf_dataset_id(project: dict) -> str:
    return project.get("hf_dataset_id", "").strip()


def _hf_token(project: dict) -> str:
    return (project.get("env_vars", {}).get("BOT_DATA_TOKEN", "")
            or os.environ.get("BOT_DATA_TOKEN", "")
            or os.environ.get("HF_TOKEN", "")).strip()


def _hf_api(token: str):
    """Retorna una instancia de HfApi lista para usar."""
    try:
        from huggingface_hub import HfApi
        return HfApi(token=token)
    except ImportError:
        return None


def _safe_read(path: Path, retries=3, delay=1.5):
    """Lee un archivo con reintentos por si está siendo escrito por el bot."""
    for attempt in range(retries):
        try:
            data = path.read_bytes()
            if len(data) > 0:
                return data
        except (OSError, PermissionError, FileNotFoundError):
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def _hf_folder_key(project: dict) -> str:
    """
    Nombre de carpeta en el dataset HF para este proyecto.
    Usa el nombre del proyecto (sanitizado) en lugar del pid efímero,
    de modo que si el Space se reinicia el proyecto siempre encuentra
    su carpeta correcta aunque el pid haya cambiado.
    Ejemplo: proyecto "Mi Bot" → carpeta "Mi_Bot"
    """
    name = (project.get("name") or "").strip()
    if not name:
        # Fallback al pid si por alguna razón no hay nombre
        return project.get("id", "unknown")
    # Reemplazar caracteres problemáticos en rutas por guion bajo
    safe = re.sub(r'[\\/:*?"<>| ]', "_", name)
    # Eliminar guiones bajos múltiples consecutivos
    safe = re.sub(r'_+', "_", safe).strip("_")
    return safe or project.get("id", "unknown")


def hf_dataset_is_empty(pid: str, project: dict) -> bool:
    dataset_id = _hf_dataset_id(project)
    token      = _hf_token(project)
    if not dataset_id or not token:
        return True
    try:
        api   = _hf_api(token)
        files = api.list_repo_files(repo_id=dataset_id, repo_type="dataset", revision="main")
        # Buscar si hay algún archivo dentro de la carpeta del proyecto
        folder = _hf_folder_key(project)
        for f in files:
            if f.startswith(f"{folder}/"):
                return False
        return True
    except Exception:
        return True


def hf_save_project_config(pid: str, project: dict):
    """
    Guarda un archivo .nexhost_config.json en el dataset junto a los archivos del proyecto.
    Esto permite que al reiniciar el Space, el proyecto pueda arrancar automáticamente
    incluso si projects.json no está disponible localmente.
    """
    dataset_id = _hf_dataset_id(project)
    token      = _hf_token(project)
    if not dataset_id or not token:
        return
    api = _hf_api(token)
    if api is None:
        return
    folder = _hf_folder_key(project)
    config_data = {
        "id":             project.get("id"),
        "name":           project.get("name"),
        "start_cmd":      project.get("start_cmd", ""),
        "deps_file":      project.get("deps_file", ""),
        "python_version": project.get("python_version", "3.11"),
        "branch":         project.get("branch", "main"),
        "repo_url":       project.get("repo_url", ""),
        "language":       project.get("language", ""),
        "no_web":         project.get("no_web", False),
        "auto_restart":   project.get("auto_restart", True),
        "auto_deploy":    project.get("auto_deploy", True),
        "root_dir":       project.get("root_dir", ""),
        "hf_dataset_id":  dataset_id,
        "persist_data":   project.get("persist_data", False),
        "env_vars":       project.get("env_vars", {}),
        "_nexhost_config_version": 1,
        "_saved_at": datetime.now().isoformat(),
    }
    try:
        import io as _io
        api.upload_file(
            path_or_fileobj=_io.BytesIO(json.dumps(config_data, indent=2).encode()),
            path_in_repo=f"{folder}/.nexhost_config.json",
            repo_id=dataset_id,
            repo_type="dataset",
            commit_message="nexhost: save project startup config",
        )
        append_log(pid, "[HF] ✓ Config de arranque guardada en dataset (.nexhost_config.json)")
    except Exception as e:
        append_log(pid, f"[HF] ⚠ Error guardando config de arranque: {e}")


def hf_upload_directory(pid: str, project: dict, work_dir: Path):
    """Sube todos los archivos usando huggingface_hub (maneja LFS automáticamente)."""
    dataset_id = _hf_dataset_id(project)
    token      = _hf_token(project)
    if not dataset_id or not token:
        return

    api = _hf_api(token)
    if api is None:
        append_log(pid, "[HF] ✕ huggingface_hub no está instalado")
        return

    files = [
        f for f in work_dir.rglob("*")
        if f.is_file()
        and not _should_skip_file(f)
    ]
    if not files:
        append_log(pid, "[HF] ⚠ No hay archivos para subir")
        return

    folder = _hf_folder_key(project)
    append_log(pid, f"[HF] ⬆ Subiendo {len(files)} archivos con huggingface_hub...")
    ok_count   = 0
    fail_count = 0

    for f in files:
        rel = str(f.relative_to(work_dir)).replace("\\", "/")
        path_in_repo = f"{folder}/{rel}"
        for attempt in range(3):
            try:
                api.upload_file(
                    path_or_fileobj=str(f),
                    path_in_repo=path_in_repo,
                    repo_id=dataset_id,
                    repo_type="dataset",
                    commit_message=f"nexhost: sync {rel}",
                )
                ok_count += 1
                break
            except Exception as e:
                err = str(e)
                if "429" in err:
                    time.sleep((attempt + 1) * 10)
                elif attempt < 2:
                    time.sleep((attempt + 1) * 3)
                else:
                    append_log(pid, f"[HF] ⚠ Error subiendo {rel}: {e}")
                    fail_count += 1

    append_log(pid, f"[HF] ✓ {ok_count}/{len(files)} archivos subidos"
                    + (f" ({fail_count} errores)" if fail_count else ""))
    # Guardar config de arranque junto a los archivos del proyecto
    hf_save_project_config(pid, project)


def hf_download_dataset(pid: str, project: dict, work_dir: Path) -> bool:
    """Descarga todos los archivos del dataset usando huggingface_hub."""
    dataset_id = _hf_dataset_id(project)
    token      = _hf_token(project)
    if not dataset_id or not token:
        append_log(pid, "[HF] ✕ Falta BOT_DATA_TOKEN / HF_TOKEN")
        return False

    api = _hf_api(token)
    if api is None:
        append_log(pid, "[HF] ✕ huggingface_hub no está instalado")
        return False

    append_log(pid, "[HF] ⬇ Descargando archivos del dataset...")
    try:
        all_files = list(api.list_repo_files(
            repo_id=dataset_id, repo_type="dataset", revision="main"
        ))
        folder = _hf_folder_key(project)
        project_files = [f for f in all_files if f.startswith(f"{folder}/")]

        if not project_files:
            work_dir.mkdir(parents=True, exist_ok=True)
            return True

        work_dir.mkdir(parents=True, exist_ok=True)
        ok = 0
        for fpath in project_files:
            local_rel = fpath[len(folder)+1:]
            dest = work_dir / local_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(3):
                try:
                    from huggingface_hub import hf_hub_download
                    hf_hub_download(
                        repo_id=dataset_id,
                        filename=fpath,
                        repo_type="dataset",
                        local_dir=str(work_dir.parent),
                        token=token,
                    )
                    # hf_hub_download guarda en local_dir/fpath, mover a dest
                    downloaded = work_dir.parent / fpath
                    if downloaded.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        downloaded.replace(dest)
                    ok += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep((attempt + 1) * 2)
                    else:
                        append_log(pid, f"[HF] ⚠ Error descargando {local_rel}: {e}")

        append_log(pid, f"[HF] ✓ {ok}/{len(project_files)} archivos descargados")
        return True

    except Exception as e:
        append_log(pid, f"[HF] ✕ Error: {e}")
        return False


def hf_upload_file(pid: str, project: dict, local_path: Path, work_dir: Path):
    """Sube un único archivo modificado al dataset HF."""
    dataset_id = _hf_dataset_id(project)
    token      = _hf_token(project)
    if not dataset_id or not token:
        return

    api = _hf_api(token)
    if api is None:
        return

    folder = _hf_folder_key(project)
    rel = str(local_path.relative_to(work_dir)).replace("\\", "/")
    path_in_repo = f"{folder}/{rel}"
    for attempt in range(3):
        try:
            api.upload_file(
                path_or_fileobj=str(local_path),
                path_in_repo=path_in_repo,
                repo_id=dataset_id,
                repo_type="dataset",
                commit_message=f"nexhost: update {rel}",
            )
            append_log(pid, f"[HF] ⬆ Subido: {rel}", "process")
            return
        except Exception as e:
            if attempt < 2:
                time.sleep((attempt + 1) * 3)
            else:
                append_log(pid, f"[HF] ⚠ Error subiendo {rel}: {e}", "process")


def start_hf_watcher(pid: str, project: dict, work_dir: Path):
    """Inicia el watcher con threading.Event para parada limpia."""
    with _hf_watcher_lock:
        existing = _hf_watchers.get(pid)
        if existing and existing["thread"].is_alive():
            return
        stop_event = threading.Event()

    def _watch(stop: threading.Event):
        pending: dict[str, float] = {}
        DEBOUNCE = 10  # segundos estable antes de subir

        def _scan() -> dict[str, float]:
            result = {}
            try:
                for f in work_dir.rglob("*"):
                    if not f.is_file(): continue
                    if _should_skip_file(f): continue
                    sz = f.stat().st_size
                    if sz < 1_024 or sz > 50_000_000: continue
                    result[str(f)] = f.stat().st_mtime
            except Exception:
                pass
            return result

        last_mtimes = _scan()

        while not stop.wait(timeout=6):  # Interruptible, sale limpio al hacer stop.set()
            current = _scan()
            now = time.time()

            for f_str, mtime in current.items():
                if last_mtimes.get(f_str) != mtime:
                    pending[f_str] = now

            # Limpiar archivos eliminados del pending
            for f_str in set(pending) - set(current):
                pending.pop(f_str, None)

            last_mtimes = current

            # Subir solo archivos que llevan DEBOUNCE segundos sin cambios
            ready = [f for f, t in list(pending.items()) if now - t >= DEBOUNCE]
            for f_str in ready:
                pending.pop(f_str, None)
                try:
                    hf_upload_file(pid, project, Path(f_str), work_dir)
                except Exception as e:
                    append_log(pid, f"[HF watcher] Error subiendo {Path(f_str).name}: {e}")

    t = threading.Thread(target=_watch, args=(stop_event,), daemon=True)
    t.start()
    with _hf_watcher_lock:
        _hf_watchers[pid] = {"thread": t, "stop": stop_event}


def stop_hf_watcher(pid: str):
    """Detiene el watcher de forma limpia señalando el Event."""
    with _hf_watcher_lock:
        entry = _hf_watchers.pop(pid, None)
    if entry:
        entry["stop"].set()


def _purge_hf_project_folder(project: dict):
    """Borra la carpeta del proyecto en el HF Dataset (al eliminar el proyecto)."""
    try:
        dataset_id = _hf_dataset_id(project)
        if not dataset_id:
            return
        from huggingface_hub import HfApi  # type: ignore
        token = os.environ.get("BOT_DATA_TOKEN", "")
        if not token:
            return
        api = HfApi(token=token)
        try:
            api.delete_folder(
                path_in_repo=project["id"],
                repo_id=dataset_id,
                repo_type="dataset",
                commit_message=f"Delete project {project['id']}",
            )
        except Exception as e:
            print(f"[hf-purge] {project.get('id')}: {e}")
    except Exception as e:
        print(f"[hf-purge] error: {e}")


def _ensure_uv():
    """
    Descarga uv UNA sola vez al directorio persistente.
    uv es un binario estático (~15MB) que gestiona setuptools internamente.
    """
    if UV_BIN.exists() and os.access(str(UV_BIN), os.X_OK):
        print(f"   ✓ uv encontrado en {UV_BIN}")
        return str(UV_BIN)

    # Intentar usar uv del sistema primero
    uv_sys = shutil.which("uv")
    if uv_sys:
        print(f"   ✓ uv del sistema: {uv_sys}")
        return uv_sys

    print("   🔧 Descargando uv (binario estático ~15MB)...")
    try:
        # Instalar uv usando el script oficial de astral.sh
        install_script = BIN_DIR / "install_uv.sh"
        req = urllib.request.Request(
            "https://astral.sh/uv/install.sh",
            headers={"User-Agent": "NexHost/3.0"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            install_script.write_bytes(resp.read())
        install_script.chmod(0o755)

        env = {"HOME": str(BIN_DIR), "UV_INSTALL_DIR": str(BIN_DIR), "PATH": "/usr/local/bin:/usr/bin:/bin"}
        r = subprocess.run(
            ["sh", str(install_script)],
            capture_output=True, text=True, timeout=120, env=env
        )
        install_script.unlink(missing_ok=True)

        if UV_BIN.exists() and os.access(str(UV_BIN), os.X_OK):
            rv = subprocess.run([str(UV_BIN), "--version"], capture_output=True, text=True, timeout=10)
            print(f"   ✓ {rv.stdout.strip()} instalado")
            return str(UV_BIN)

        print("   ⚠ uv no se pudo instalar automáticamente — usando pip del sistema")
    except Exception as e:
        print(f"   ⚠ No se pudo instalar uv: {e}")

    return None

# Variable global del motor — se inicializa en background para no bloquear el arranque
UV = None
_uv_ready = threading.Event()

def _init_uv_background():
    global UV
    UV = _ensure_uv()
    _uv_ready.set()

threading.Thread(target=_init_uv_background, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# MOTOR UNIVERSAL DE INSTALACIÓN — mise (rtx) / asdf
# ══════════════════════════════════════════════════════════════════════════════
MISE_BIN = BIN_DIR / "mise"
_mise_ready = threading.Event()
_MISE = None  # ruta al binario mise (o None si no disponible)

def _ensure_mise() -> str | None:
    """
    Instala mise (https://mise.jdx.dev) una sola vez al directorio persistente.
    mise es un gestor de versiones multi-lenguaje (reemplaza asdf, nvm, pyenv, etc.).
    Soporta instalación de versiones EXACTAS de Python, Node, Go, Rust, etc.
    """
    global _MISE
    if MISE_BIN.exists() and os.access(str(MISE_BIN), os.X_OK):
        _MISE = str(MISE_BIN)
        return _MISE

    sys_mise = shutil.which("mise")
    if sys_mise:
        _MISE = sys_mise
        return _MISE

    print("   🔧 Descargando mise (gestor universal de runtimes)...")
    try:
        script = BIN_DIR / "install_mise.sh"
        req = urllib.request.Request(
            "https://mise.run",
            headers={"User-Agent": "NexHost/3.0"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            script.write_bytes(resp.read())
        script.chmod(0o755)

        env = {
            "HOME": str(BIN_DIR),
            "MISE_INSTALL_PATH": str(MISE_BIN),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
        r = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=120, env=env)
        script.unlink(missing_ok=True)

        if MISE_BIN.exists() and os.access(str(MISE_BIN), os.X_OK):
            rv = subprocess.run([str(MISE_BIN), "--version"], capture_output=True, text=True, timeout=10)
            print(f"   ✓ mise {rv.stdout.strip()} instalado")
            _MISE = str(MISE_BIN)
            return _MISE
    except Exception as e:
        print(f"   ⚠ mise no disponible: {e}")
    return None

def _init_mise_background():
    global _MISE
    _MISE = _ensure_mise()
    _mise_ready.set()

threading.Thread(target=_init_mise_background, daemon=True).start()


def _parse_version_precision(ver: str) -> tuple[str, str]:
    """
    Analiza la precisión de la versión solicitada.

    Returns:
        ('exact',  '3.11.8')  → instalar exactamente esa versión (3 segmentos)
        ('latest', '3.11')    → instalar la última estable de esa rama (2 segmentos)
        ('major',  '3')       → instalar la última estable del major (1 segmento)
    """
    if not ver:
        return ('latest', '3.11')
    parts = [p.strip() for p in ver.strip().split('.') if p.strip().isdigit()]
    if len(parts) >= 3:
        return ('exact', '.'.join(parts[:3]))
    elif len(parts) == 2:
        return ('latest', '.'.join(parts[:2]))
    elif len(parts) == 1:
        return ('major', parts[0])
    return ('latest', '3.11')


def install_runtime_with_mise(lang: str, version: str, log_fn) -> str | None:
    """
    Instala un runtime (python, node, go, rust...) a la versión solicitada usando mise.
    Respeta la precisión: 3 números → versión exacta; 2 números → última de la rama.

    Args:
        lang:    'python' | 'node' | 'go' | 'rust' | 'ruby' | etc.
        version: '3.11.8' (exacta) | '3.11' (rama) | '20' (major node)
        log_fn:  función de log

    Returns:
        Ruta al binario instalado, o None si falló.
    """
    # Esperar a que mise esté listo
    if not _MISE:
        _mise_ready.wait(timeout=60)
    if not _MISE:
        log_fn(f"[mise] ⚠ mise no disponible — usando fallback")
        return None

    precision, ver_spec = _parse_version_precision(version)

    if precision == 'exact':
        install_spec = f"{lang}@{ver_spec}"
        log_fn(f"[mise] 🎯 Instalando {lang} {ver_spec} (versión EXACTA)")
    elif precision == 'latest':
        install_spec = f"{lang}@{ver_spec}"
        log_fn(f"[mise] 📦 Instalando {lang} {ver_spec}.x (última estable de la rama)")
    else:
        install_spec = f"{lang}@latest"
        log_fn(f"[mise] 📦 Instalando {lang} (última versión estable)")

    mise_env = {
        "HOME": str(BIN_DIR),
        "PATH": f"{BIN_DIR}:/usr/local/bin:/usr/bin:/bin",
        "MISE_DATA_DIR": str(PERSIST / "mise"),
        "MISE_CACHE_DIR": str(PERSIST / "mise" / "cache"),
        "MISE_CONFIG_DIR": str(PERSIST / "mise" / "config"),
        "LANG": "en_US.UTF-8",
    }

    # Asegurar que los directorios de mise existen
    (PERSIST / "mise").mkdir(parents=True, exist_ok=True)
    (PERSIST / "mise" / "cache").mkdir(parents=True, exist_ok=True)
    (PERSIST / "mise" / "config").mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.Popen(
            [_MISE, "install", install_spec],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=mise_env
        )
        _ANSI_M = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
        for line in iter(proc.stdout.readline, ""):
            clean = _ANSI_M.sub('', line).rstrip()
            if clean and not clean.startswith("mise "):
                log_fn(f"[mise] {clean}")
        proc.wait()

        if proc.returncode != 0:
            log_fn(f"[mise] ✕ Instalación falló (código {proc.returncode})")
            return None

        # Obtener la ruta del binario instalado
        which_proc = subprocess.run(
            [_MISE, "which", lang, "--install-spec", install_spec],
            capture_output=True, text=True, timeout=15, env=mise_env
        )
        if which_proc.returncode == 0 and which_proc.stdout.strip():
            bin_path = which_proc.stdout.strip()
            log_fn(f"[mise] ✓ {lang} instalado: {bin_path}")
            return bin_path

        # Fallback: buscar el binario manualmente
        which_fallback = subprocess.run(
            [_MISE, "exec", install_spec, "--", f"which {lang}"],
            capture_output=True, text=True, timeout=15, env=mise_env, shell=False
        )
        if which_fallback.returncode == 0 and which_fallback.stdout.strip():
            bin_path = which_fallback.stdout.strip()
            log_fn(f"[mise] ✓ {lang} en: {bin_path}")
            return bin_path

        log_fn(f"[mise] ✓ {lang} instalado (binario en PATH de mise)")
        return _MISE  # mise exec manejará el path

    except Exception as e:
        log_fn(f"[mise] ✕ Error: {e}")
        return None


def get_mise_env(lang: str, version: str) -> dict:
    """Devuelve las variables de entorno de mise para ejecutar un runtime instalado."""
    if not _MISE:
        return {}
    precision, ver_spec = _parse_version_precision(version)
    mise_data = str(PERSIST / "mise")
    return {
        "MISE_DATA_DIR": mise_data,
        "MISE_CACHE_DIR": str(PERSIST / "mise" / "cache"),
        "MISE_CONFIG_DIR": str(PERSIST / "mise" / "config"),
        "MISE_RUNTIME_VERSION": ver_spec,
        "PATH": f"{mise_data}/shims:{BIN_DIR}:/usr/local/bin:/usr/bin:/bin",
    }


def _get_env_dir(pid):
    """Retorna la ruta del entorno aislado de un proyecto (en /tmp)."""
    return ENVS_BASE / pid


def _env_python(pid):
    """Retorna el binario python dentro del entorno del proyecto."""
    return _get_env_dir(pid) / "bin" / "python"


def setup_isolated_env(pid, py_version, log_fn, force=False):
    """
    Prepara el entorno de Python para el proyecto.

    MOTOR UNIVERSAL DE VERSIONES:
    ─────────────────────────────
    • VERSION con 3 segmentos (ej: 3.11.8)  → instala EXACTAMENTE esa versión
      - Primero intenta con mise (descarga python-build-standalone)
      - Segundo fallback: uv (también soporta versiones exactas)

    • VERSION con 2 segmentos (ej: 3.11)    → instala la ÚLTIMA estable de esa rama
      - Usa uv (más rápido, ya disponible)

    • Sin version / 1 segmento              → última estable (defecto: 3.11)

    NO usa apt/yum ni repositorios del SO (que solo tienen versiones aproximadas).
    """
    env_dir    = _get_env_dir(pid)
    python_bin = env_dir / "bin" / "python"
    pip_bin    = env_dir / "bin" / "pip"

    precision, ver_spec = _parse_version_precision(py_version or "3.11")
    py_ver_spec = ver_spec  # versión canónica que se usará

    log_fn(f"[Build] Versión solicitada: {py_version or '3.11'} → precisión: {precision} → spec: {py_ver_spec}")

    # Reutilizar si ya existe y la versión coincide
    marker = env_dir / ".nx_py_version"
    if python_bin.exists() and not force:
        saved = marker.read_text().strip() if marker.exists() else ""
        if saved == py_ver_spec:
            log_fn(f"[Build] ✓ Entorno reutilizado (Python {py_ver_spec})")
            return str(python_bin), str(pip_bin)
        else:
            log_fn(f"[Build] ♻ Versión cambiada ({saved} → {py_ver_spec}) — recreando entorno...")
            shutil.rmtree(str(env_dir), ignore_errors=True)

    env_dir.mkdir(parents=True, exist_ok=True)

    # ── ESTRATEGIA DE INSTALACIÓN ─────────────────────────────────────────
    #
    # VERSIÓN EXACTA (3 segmentos): priorizar mise > uv
    # VERSIÓN DE RAMA (2 segmentos): usar uv directamente (más rápido)
    #
    installed_python = None

    if precision == 'exact':
        log_fn(f"[Build] 🎯 Modo EXACTO: instalando Python {py_ver_spec} (sin aproximación)")

        # Intento 1: mise (soporte nativo de versiones exactas)
        _mise_ready.wait(timeout=45)
        if _MISE:
            log_fn(f"[Build]   → Usando mise para versión exacta {py_ver_spec}...")
            mise_python = install_runtime_with_mise('python', py_ver_spec, log_fn)
            if mise_python and mise_python != _MISE:
                # mise devolvió el path del binario python
                _create_venv_from_python(pid, mise_python, env_dir, py_ver_spec, log_fn)
                if python_bin.exists():
                    installed_python = str(python_bin)
            elif mise_python == _MISE:
                # mise está disponible pero which no devolvió path; intentar con exec
                log_fn(f"[Build]   → Creando venv vía mise exec python...")
                mise_env = get_mise_env('python', py_ver_spec)
                try:
                    r = subprocess.run(
                        [_MISE, "exec", f"python@{py_ver_spec}", "--", "python3", "-m", "venv", str(env_dir)],
                        capture_output=True, text=True, timeout=180,
                        env={**os.environ, **mise_env}
                    )
                    if r.returncode == 0 and python_bin.exists():
                        installed_python = str(python_bin)
                        log_fn(f"[Build]   ✓ venv creado con mise exec")
                except Exception as e:
                    log_fn(f"[Build]   ⚠ mise exec falló: {e}")

        # Intento 2: uv (python-build-standalone también soporta versiones exactas)
        if not installed_python:
            _uv_ready.wait(timeout=60)
            if UV:
                log_fn(f"[Build]   → Usando uv para versión exacta {py_ver_spec}...")
                _ok = _create_venv_with_uv(pid, py_ver_spec, env_dir, log_fn)
                if _ok and python_bin.exists():
                    installed_python = str(python_bin)

    else:
        # VERSIÓN DE RAMA (2 segmentos) o major: uv es suficiente
        log_fn(f"[Build] 📦 Modo RAMA: instalando Python {py_ver_spec}.x (última estable)")
        _uv_ready.wait(timeout=60)
        if UV:
            _ok = _create_venv_with_uv(pid, py_ver_spec, env_dir, log_fn)
            if _ok and python_bin.exists():
                installed_python = str(python_bin)

    # Fallback final: venv del sistema
    if not installed_python:
        log_fn(f"[Build] ⚠ Usando Python del sistema como fallback...")
        return _setup_venv_fallback(pid, py_version, log_fn)

    # ── Verificar versión real instalada ─────────────────────────────────
    rv = subprocess.run([installed_python, "--version"], capture_output=True, text=True, timeout=10)
    real_ver = (rv.stdout.strip() or rv.stderr.strip()).replace("Python ", "")
    log_fn(f"[Build] ✓ Python {real_ver} instalado en {env_dir}")

    if precision == 'exact':
        # Verificar que la versión exacta coincide
        if real_ver.startswith(py_ver_spec):
            log_fn(f"[Build] ✓ Versión exacta confirmada: {real_ver} == {py_ver_spec}")
        else:
            log_fn(f"[Build] ⚠ ATENCIÓN: se solicitó {py_ver_spec} pero se instaló {real_ver}")

    # ── Inyectar herramientas base ────────────────────────────────────────
    _seed_venv(pid, installed_python, env_dir, log_fn)

    marker.write_text(py_ver_spec)
    _clean_uv_cache(log_fn)
    return installed_python, str(pip_bin)


def _create_venv_with_uv(pid: str, py_ver_spec: str, env_dir, log_fn) -> bool:
    """Crea un venv usando uv venv --python <spec>. Retorna True si tuvo éxito."""
    if not UV:
        return False

    uv_env = {
        "HOME": str(ENVS_BASE),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "UV_PYTHON_PREFERENCE": "only-managed",  # fuerza python-build-standalone (versiones exactas)
    }

    log_fn(f"[Build]   $ uv venv --python {py_ver_spec}")
    try:
        proc = subprocess.Popen(
            [str(UV), "venv", str(env_dir), "--python", py_ver_spec],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=uv_env
        )
        _ANSI_UV = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
        for line in iter(proc.stdout.readline, ""):
            clean = _ANSI_UV.sub('', line).rstrip()
            if clean and not clean.startswith("warning:"):
                log_fn(f"[uv] {clean}")
        proc.wait()
        return proc.returncode == 0
    except Exception as e:
        log_fn(f"[Build]   ✕ uv venv error: {e}")
        return False


def _create_venv_from_python(pid: str, python_path: str, env_dir, py_ver_spec: str, log_fn):
    """Crea un venv usando el binario python especificado."""
    log_fn(f"[Build]   $ {python_path} -m venv {env_dir}")
    try:
        r = subprocess.run(
            [python_path, "-m", "venv", str(env_dir)],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            log_fn(f"[Build]   ✕ venv creation failed: {r.stderr[-200:]}")
    except Exception as e:
        log_fn(f"[Build]   ✕ Error: {e}")


def _seed_venv(pid: str, python_bin: str, env_dir, log_fn):
    """Inyecta pip + setuptools + wheel en el venv."""
    log_fn("[Build] ⏳ Inyectando pip + setuptools + wheel...")
    _seed_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(ENVS_BASE),
        "LANG": "en_US.UTF-8",
        "UV_NO_CACHE": "1",
    }

    # Intento 1: uv pip install --python <venv>
    if UV:
        _seed = subprocess.run(
            [str(UV), "pip", "install", "--python", python_bin,
             "pip", "setuptools", "wheel"],
            capture_output=True, text=True, timeout=120, env=_seed_env
        )
        if _seed.returncode == 0:
            log_fn("[Build] ✓ pip + setuptools + wheel listos")
            return

    # Intento 2: ensurepip
    _ep = subprocess.run(
        [python_bin, "-m", "ensurepip", "--upgrade"],
        capture_output=True, text=True, timeout=60
    )
    if _ep.returncode == 0:
        subprocess.run(
            [python_bin, "-m", "pip", "install", "--upgrade",
             "pip", "setuptools", "wheel", "--quiet", "--no-cache-dir"],
            capture_output=True, text=True, timeout=120,
            env={"PATH": f"{env_dir}/bin:/usr/local/bin:/usr/bin:/bin",
                 "HOME": str(ENVS_BASE), "LANG": "en_US.UTF-8",
                 "VIRTUAL_ENV": str(env_dir)}
        )
        log_fn("[Build] ✓ pip + setuptools + wheel listos (ensurepip)")
    else:
        log_fn("[Build] ⚠ No se pudieron inyectar herramientas base (continuando...)")


def _setup_venv_fallback(pid, py_version, log_fn):
    """
    Fallback: crea un venv estándar cuando uv no está disponible.
    Usa el Python del sistema pero aislado del resto del servidor.
    """
    log_fn(f"[Build] 🔄 Fallback: creando venv aislado con Python del sistema...")
    env_dir = _get_env_dir(pid)
    python_bin = env_dir / "bin" / "python"
    pip_bin    = env_dir / "bin" / "pip"
    env_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, "-m", "venv", str(env_dir)],
        capture_output=True, timeout=60
    )
    if r.returncode != 0:
        log_fn(f"[Build] ✕ venv fallback falló: {r.stderr.decode()[-200:]}")
        return sys.executable, shutil.which("pip3") or "pip3"
    log_fn(f"[Build] ✓ venv fallback creado en {env_dir}")

    # Inyectar herramientas base también en el fallback
    log_fn("[Build] ⏳ Inyectando pip + setuptools + wheel...")
    _fb_env = {
        "PATH": f"{env_dir / 'bin'}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(ENVS_BASE),
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "VIRTUAL_ENV": str(env_dir),
    }
    _inj = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--upgrade",
         "pip", "setuptools", "wheel", "--quiet", "--no-cache-dir"],
        capture_output=True, text=True, timeout=120, env=_fb_env
    )
    if _inj.returncode == 0:
        log_fn("[Build] ✓ pip + setuptools + wheel listos")
    else:
        log_fn(f"[Build] ⚠ Herramientas base: {_inj.stderr[-200:]}")

    return str(python_bin), str(pip_bin)


def install_deps_isolated(pid, deps_file, work_dir, python_bin, pip_bin,
                           extra_env, log_fn):
    """
    Instala dependencias del proyecto en su entorno aislado.
    Soporta: Python (pip), Node.js (npm con versión exacta vía mise), Go, Ruby.
    """
    deps_path = Path(work_dir) / deps_file
    if not deps_path.exists():
        log_fn(f"[nexhost] ⚠ {deps_file} no encontrado — omitiendo")
        return

    env_dir = _get_env_dir(pid)
    _sys_path = "/usr/local/bin:/usr/bin:/bin"
    _python_env_dir = env_dir
    _using_venv = (env_dir / "bin" / "python").exists()

    if _using_venv and "requirements" in deps_file:
        _install_path = f"{_python_env_dir / 'bin'}:{_sys_path}"
        _virtual_env  = str(_python_env_dir)
    else:
        _install_path = _sys_path
        _virtual_env  = ""

    install_env = {
        "PATH": _install_path,
        "HOME": str(ENVS_BASE),
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PIP_NO_CACHE_DIR": "1",
    }
    if _virtual_env:
        install_env["VIRTUAL_ENV"] = _virtual_env
    install_env.update(extra_env or {})

    if "requirements" in deps_file:
        log_fn(f"[nexhost] $ python -m pip install -r {deps_file}")
        log_fn("[nexhost] ⏳ Instalando dependencias Python...")
        pip_cmd = [python_bin, "-m", "pip", "install",
                   "-r", str(deps_path),
                   "--no-cache-dir",
                   "--disable-pip-version-check"]
        pip_proc = subprocess.Popen(
            pip_cmd, cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        _ANSI_P = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
        _PIP_NOISE = re.compile(
            r'Searching for|Found candidates|Available versions'
            r'|Looking in indexes|Looking in links|Could not find a version'
            r'|Skipping link|^\s*[-]{3,}|Requirement already'
            r'|Using cached.*\.whl|pip is looking at multiple versions'
            r'|This could take a while|MB/s|kB/s'
        )
        for line in iter(pip_proc.stdout.readline, ""):
            if not line.strip(): continue
            clean = _ANSI_P.sub('', line).rstrip()
            if not clean or _PIP_NOISE.search(clean): continue
            log_fn(f"[pip] {clean}")
        pip_proc.wait()
        if pip_proc.returncode == 0:
            log_fn("[nexhost] ✓ Dependencias instaladas correctamente")
        else:
            log_fn(f"[nexhost] ✕ pip falló (código {pip_proc.returncode})")
        _clean_uv_cache(log_fn)

    elif deps_file == "package.json":
        # ── Detectar y respetar versión de Node.js del proyecto ──────────
        node_ver = _detect_node_version(work_dir, log_fn)
        npm_path, node_env_extra = _get_node_npm(node_ver, log_fn)
        install_env.update(node_env_extra)

        log_fn(f"[nexhost] $ npm install")
        log_fn("[nexhost] ⏳ Instalando dependencias Node.js...")
        npm_proc = subprocess.Popen(
            [npm_path, "install", "--no-fund", "--no-audit"],
            cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        _ANSI_N = re.compile(r'\x1b\[[0-9;]*m')
        _NPM_NOISE = re.compile(r'(^npm warn|^npm notice|^\s*$|^-+$)')
        for line in iter(npm_proc.stdout.readline, ""):
            if not line.strip(): continue
            clean = _ANSI_N.sub('', line).rstrip()
            if not clean or _NPM_NOISE.search(clean): continue
            log_fn(f"[npm] {clean}")
        npm_proc.wait()
        log_fn("[nexhost] ✓ Node deps instaladas" if npm_proc.returncode == 0
               else f"[nexhost] ✕ npm falló (código {npm_proc.returncode})")

    elif deps_file in ("go.mod", "go.sum"):
        go = shutil.which("go") or "go"
        log_fn("[nexhost] $ go mod download")
        go_proc = subprocess.Popen(
            [go, "mod", "download"],
            cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        for line in iter(go_proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped:
                log_fn(f"[go] {stripped}")
        go_proc.wait()
        log_fn("[nexhost] ✓ Go modules descargados" if go_proc.returncode == 0
               else "[nexhost] ✕ go mod failed")

    elif deps_file == "Gemfile":
        log_fn("[nexhost] $ bundle install")
        bundle_proc = subprocess.Popen(
            ["bundle", "install"],
            cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        for line in iter(bundle_proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped:
                log_fn(f"[bundle] {stripped}")
        bundle_proc.wait()
        log_fn("[nexhost] ✓ Ruby gems instaladas" if bundle_proc.returncode == 0
               else "[nexhost] ✕ bundle failed")

    elif deps_file == "Cargo.toml":
        cargo = shutil.which("cargo") or "cargo"
        log_fn("[nexhost] $ cargo fetch")
        log_fn("[nexhost] ⏳ Descargando dependencias Rust...")
        cargo_proc = subprocess.Popen(
            [cargo, "fetch"], cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        for line in iter(cargo_proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped: log_fn(f"[cargo] {stripped}")
        cargo_proc.wait()
        log_fn("[nexhost] ✓ Dependencias Rust descargadas" if cargo_proc.returncode == 0
               else f"[nexhost] ✕ cargo fetch falló (código {cargo_proc.returncode})")

    elif deps_file == "composer.json":
        composer = shutil.which("composer") or "composer"
        log_fn("[nexhost] $ composer install --no-dev")
        log_fn("[nexhost] ⏳ Instalando dependencias PHP...")
        composer_proc = subprocess.Popen(
            [composer, "install", "--no-dev", "--optimize-autoloader", "--no-interaction"],
            cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        for line in iter(composer_proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped: log_fn(f"[composer] {stripped}")
        composer_proc.wait()
        log_fn("[nexhost] ✓ Dependencias PHP instaladas" if composer_proc.returncode == 0
               else f"[nexhost] ✕ composer falló (código {composer_proc.returncode})")

    elif deps_file == "pom.xml":
        mvn = shutil.which("mvn") or "mvn"
        log_fn("[nexhost] $ mvn dependency:resolve -q")
        log_fn("[nexhost] ⏳ Resolviendo dependencias Maven...")
        mvn_proc = subprocess.Popen(
            [mvn, "dependency:resolve", "-q", "--no-transfer-progress"],
            cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        for line in iter(mvn_proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped: log_fn(f"[mvn] {stripped}")
        mvn_proc.wait()
        log_fn("[nexhost] ✓ Dependencias Maven resueltas" if mvn_proc.returncode == 0
               else f"[nexhost] ✕ mvn falló (código {mvn_proc.returncode})")

    elif deps_file == "build.gradle":
        gradlew = str(Path(work_dir) / "gradlew")
        gradle = gradlew if Path(gradlew).exists() else (shutil.which("gradle") or "gradle")
        if Path(gradlew).exists(): Path(gradlew).chmod(0o755)
        log_fn("[nexhost] $ gradle dependencies")
        log_fn("[nexhost] ⏳ Resolviendo dependencias Gradle...")
        gradle_proc = subprocess.Popen(
            [gradle, "dependencies", "--no-daemon", "-q"],
            cwd=str(work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=install_env
        )
        for line in iter(gradle_proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped: log_fn(f"[gradle] {stripped}")
        gradle_proc.wait()
        log_fn("[nexhost] ✓ Dependencias Gradle resueltas" if gradle_proc.returncode == 0
               else f"[nexhost] ✕ gradle falló (código {gradle_proc.returncode})")

    elif deps_file == "deno.json":
        deno = shutil.which("deno") or "deno"
        deno_entry = next((c for c in ["main.ts","mod.ts","index.ts","app.ts"]
                           if (Path(work_dir) / c).exists()), None)
        if deno_entry:
            log_fn(f"[nexhost] $ deno cache {deno_entry}")
            log_fn("[nexhost] ⏳ Cacheando dependencias Deno...")
            deno_proc = subprocess.Popen(
                [deno, "cache", deno_entry], cwd=str(work_dir),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=install_env
            )
            for line in iter(deno_proc.stdout.readline, ""):
                stripped = line.rstrip()
                if stripped: log_fn(f"[deno] {stripped}")
            deno_proc.wait()
            log_fn("[nexhost] ✓ Dependencias Deno cacheadas" if deno_proc.returncode == 0
                   else f"[nexhost] ✕ deno cache falló (código {deno_proc.returncode})")
        else:
            log_fn("[nexhost] ⚠ No se encontró entry point Deno (main.ts/mod.ts)")




def _clean_uv_cache(log_fn):
    """Limpia el caché de uv después de instalar dependencias."""
    if not UV:
        return
    try:
        subprocess.run([str(UV), "cache", "clean"], capture_output=True, timeout=30)
        log_fn("[Build]   ♻ Caché de uv limpiado")
    except Exception:
        pass


def _detect_node_version(work_dir, log_fn) -> "str | None":
    """Lee la versión de Node.js requerida desde .nvmrc, .node-version o package.json engines."""
    work_dir = Path(work_dir)
    for nvmrc in [".nvmrc", ".node-version"]:
        f = work_dir / nvmrc
        if f.exists():
            ver = f.read_text().strip().lstrip('v')
            if ver:
                log_fn(f"[nexhost] 📌 Versión Node detectada en {nvmrc}: {ver}")
                return ver
    pkg = work_dir / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            engines_node = data.get("engines", {}).get("node", "")
            if engines_node:
                m = re.search(r'(\d+(?:\.\d+){0,2})', engines_node)
                if m:
                    ver = m.group(1)
                    log_fn(f"[nexhost] 📌 Versión Node en package.json engines: {ver}")
                    return ver
        except Exception:
            pass
    return None


def _get_node_npm(node_ver, log_fn) -> "tuple[str, dict]":
    """
    Devuelve (path_npm, extra_env) para ejecutar npm.
    Si node_ver está definida, intenta instalar la versión exacta vía mise.
    """
    if node_ver and _MISE:
        precision, ver_spec = _parse_version_precision(node_ver)
        log_fn(f"[nexhost] 🔧 Node.js {ver_spec} ({precision}) vía mise...")
        mise_bin = install_runtime_with_mise('node', node_ver, log_fn)
        if mise_bin:
            mise_env = get_mise_env('node', node_ver)
            mise_data = Path(str(mise_env.get("MISE_DATA_DIR", str(PERSIST / "mise"))))
            npm_shim = mise_data / "shims" / "npm"
            if npm_shim.exists() and os.access(str(npm_shim), os.X_OK):
                return str(npm_shim), mise_env
            return "npm", mise_env
    npm = shutil.which("npm") or "npm"
    return npm, {}


def make_run_env(pid, internal_port, extra_vars=None):
    """
    Construye el entorno de ejecución para el proceso del proyecto.
    - HOME → directorio persistente del proyecto (datos NO se pierden al reiniciar)
    - DATA_DIR → mismo directorio persistente (conveniente para frameworks)
    - PATH → usa el Python del env aislado en /tmp
    """
    env_dir  = _get_env_dir(pid)
    # Directorio de datos persistente — sobrevive reinicios de HF Space
    data_dir = PROJECT_DATA_BASE / pid
    data_dir.mkdir(parents=True, exist_ok=True)

    run_env = {
        "PATH": f"{env_dir / 'bin'}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(data_dir),          # datos persistentes ✓
        "DATA_DIR": str(data_dir),      # alias conveniente
        "NEXHOST_DATA": str(data_dir),  # alias explícito NexHost
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "VIRTUAL_ENV": str(env_dir),
        "PORT": str(internal_port),
    }
    if extra_vars:
        for k, v in extra_vars.items():
            run_env[str(k)] = str(v)
    return run_env


# ══════════════════════════════════════════════════════════════════════════════
# DEPLOY / START
# ══════════════════════════════════════════════════════════════════════════════

def _install_github_webhook(repo_url, token, base_url):
    """Instala webhook en GitHub automáticamente usando el token del usuario."""
    try:
        parts = repo_url.rstrip("/").replace(".git","").split("github.com/")
        if len(parts) < 2: return None
        owner_repo = parts[1].strip()
        api_url    = f"https://api.github.com/repos/{owner_repo}/hooks"
        webhook_url = f"{base_url}/api/webhook"
        payload = json.dumps({
            "name": "web", "active": True, "events": ["push"],
            "config": {"url": webhook_url, "content_type": "json", "insecure_ssl": "0"}
        }).encode()
        req = urllib.request.Request(api_url, data=payload, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "NexHost/3",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("id")
    except Exception as e:
        if "422" in str(e): return "already_exists"
        print(f"[nexhost] Webhook install skipped: {e}")
        return None


def start_project(project, from_boot=False):
    pid       = project["id"]
    _root_sub = project.get("root_dir", "").strip().strip("/")
    work_dir  = BASE_DIR / pid
    exec_dir  = work_dir / _root_sub if _root_sub else work_dir
    start_cmd = project.get("start_cmd", "")
    deps_file = project.get("deps_file", "")
    branch    = project.get("branch", "main")
    repo_url  = project.get("repo_url", "")
    token     = project.get("gh_token", "") or load_config().get("gh_token", "")
    env_vars  = project.get("env_vars", {})
    py_ver    = project.get("python_version", "3.11").strip() or "3.11"

    with registry_lock:
        old_proc = registry.get(pid, {}).get("proc")
        if old_proc and old_proc.poll() is None:
            try:
                os.killpg(os.getpgid(old_proc.pid), signal.SIGTERM)
            except: old_proc.terminate()
            time.sleep(0.5)
        internal_port = (registry.get(pid, {}).get("port") or
                         project.get("internal_port") or find_free_port())
        registry[pid] = {"status": "building", "proc": None, "port": internal_port}

    # Reset restart counter
    projs_r = load_projects()
    for p_r in projs_r:
        if p_r["id"] == pid: p_r["restart_count"] = 0
    save_projects(projs_r)

    _deploy_start = time.time()
    append_log(pid, "=" * 56)
    append_log(pid, f"[nexhost] Proyecto : {project['name']}")
    append_log(pid, f"[nexhost] Motor    : uv")
    append_log(pid, f"[nexhost] Python   : {py_ver}")
    append_log(pid, f"[nexhost] Puerto   : {internal_port}")
    append_log(pid, f"[nexhost] Proxy    : /p/{pid}")
    append_log(pid, "=" * 56)

    # ── Step 1: Código fuente ────────────────────────────────────────────
    persist_data = bool(project.get("persist_data", False))
    append_log(pid, "[nexhost] ── Paso 1: Código fuente")

    _git_sha = ""
    _git_msg = ""
    _git_author = ""

    if persist_data:
        # ── MODO 2: HF Dataset como fuente de verdad ──────────────────────
        append_log(pid, "[nexhost] Modo: HF Dataset (persistente)")
        _folder_key = _hf_folder_key(project)
        append_log(pid, f"[HF] Carpeta en dataset: {_folder_key}/")

        # Si viene de restore_on_boot y ya hay archivos en disco, no repetir descarga
        already_downloaded = from_boot and (work_dir.exists() and
                              any(work_dir.iterdir()))

        if already_downloaded:
            append_log(pid, "[HF] ✓ Archivos ya en disco (descargados al iniciar contenedor)")
            with registry_lock:
                if pid in registry: registry[pid]["files_ready"] = True
            _set_project_field(pid, "files_ready", True)
        else:
            # Verificar si el dataset tiene contenido
            append_log(pid, "[HF] Verificando dataset...")
            is_empty = hf_dataset_is_empty(pid, project)

            if is_empty:
                # Primera vez — clonar GitHub y subir todo al dataset
                append_log(pid, "[HF] Dataset vacio — clonando GitHub por primera vez...")
                clone_url = repo_url
                if token and repo_url.startswith("https://github.com"):
                    clone_url = repo_url.replace("https://", f"https://{token}@")
                if work_dir.exists():
                    shutil.rmtree(work_dir, ignore_errors=True)
                append_log(pid, f"[nexhost] $ git clone -b {branch} --depth 1 <url>")
                r = subprocess.run(
                    ["git", "clone", "--depth", "1", "-b", branch, clone_url, str(work_dir)],
                    capture_output=True, text=True, timeout=180
                )
                if r.stdout.strip(): append_log(pid, r.stdout.strip())
                if r.stderr.strip(): append_log(pid, r.stderr.strip())
                if r.returncode != 0:
                    append_log(pid, "[nexhost] ✕ Clone fallido")
                    with registry_lock: registry[pid]["status"] = "error"
                    _set_project_status(pid, "error"); return
                append_log(pid, "[nexhost] ✓ Codigo clonado desde GitHub")
                hf_upload_directory(pid, project, work_dir)
                append_log(pid, "[HF] ✓ Dataset inicializado con el codigo del repo")
                with registry_lock:
                    if pid in registry: registry[pid]["files_ready"] = True
                _set_project_field(pid, "files_ready", True)
            else:
                # Dataset tiene contenido — descargar al disco
                append_log(pid, "[HF] Descargando dataset al disco...")
                if work_dir.exists():
                    shutil.rmtree(work_dir, ignore_errors=True)
                ok = hf_download_dataset(pid, project, work_dir)
                if not ok:
                    append_log(pid, "[nexhost] ✕ Fallo al descargar dataset HF")
                    with registry_lock: registry[pid]["status"] = "error"
                    _set_project_status(pid, "error"); return
                append_log(pid, "[nexhost] ✓ Archivos listos desde HF Dataset")
                with registry_lock:
                    if pid in registry: registry[pid]["files_ready"] = True
                _set_project_field(pid, "files_ready", True)

    else:
        # ── MODO 1: Espejo GitHub ─────────────────────────────────────────
        append_log(pid, "[nexhost] Modo: Espejo GitHub")
        clone_url = repo_url
        if token and repo_url.startswith("https://github.com"):
            clone_url = repo_url.replace("https://", f"https://{token}@")
        if work_dir.exists():
            append_log(pid, f"[nexhost] $ rm -rf {work_dir.name}")
            shutil.rmtree(work_dir, ignore_errors=True)
        append_log(pid, f"[nexhost] $ git clone -b {branch} --depth 1 <url>")
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch, clone_url, str(work_dir)],
            capture_output=True, text=True, timeout=180
        )
        if r.stdout.strip(): append_log(pid, r.stdout.strip())
        if r.stderr.strip(): append_log(pid, r.stderr.strip())
        if r.returncode != 0:
            append_log(pid, "[nexhost] ✕ Clone fallido")
            with registry_lock: registry[pid]["status"] = "error"
            _set_project_status(pid, "error"); return
        append_log(pid, "[nexhost] ✓ Codigo clonado desde GitHub (limpio)")
        try:
            _r = subprocess.run(
                ["git", "log", "-1", "--format=%H|%s|%an"],
                cwd=str(work_dir), capture_output=True, text=True, timeout=10
            )
            if _r.returncode == 0 and "|" in _r.stdout:
                _parts_git = _r.stdout.strip().split("|", 2)
                _git_sha    = _parts_git[0][:7]
                _git_msg    = _parts_git[1] if len(_parts_git) > 1 else ""
                _git_author = _parts_git[2] if len(_parts_git) > 2 else ""
                append_log(pid, f"[nexhost] Commit: {_git_sha} — {_git_msg}")
        except Exception:
            pass

    # runtime.txt ignorado — se usa solo la versión configurada por el usuario
    # (normalización a wildcard ocurre en setup_isolated_env)

    # ── Step 2: Detectar lenguaje y preparar entorno ────────────────────────
    append_log(pid, "[nexhost] ── Paso 2: Entorno")

    _peek_cmd  = start_cmd or ""
    _peek_deps = deps_file or ""
    # Si no tenemos info aún, hacer detección rápida
    if not _peek_cmd or not _peek_deps:
        _auto_c, _auto_d = detect_project_files(exec_dir)
        _peek_cmd  = _peek_cmd  or _auto_c  or ""
        _peek_deps = _peek_deps or _auto_d  or ""

    # Detectar lenguaje del proyecto
    # Lenguaje seleccionado explícitamente por el usuario (prioridad máxima)
    _lang_sel = project.get("language", "").lower().strip()

    _is_python = (
        _lang_sel == "python" or (not _lang_sel and (
        "requirements" in _peek_deps or "pyproject" in _peek_deps or
        _peek_cmd.startswith("python") or
        (exec_dir / "requirements.txt").exists() or
        (exec_dir / "pyproject.toml").exists()))
    )
    _is_node = (
        _lang_sel == "node" or (not _lang_sel and (
        "package.json" in _peek_deps or
        _peek_cmd.startswith("node") or _peek_cmd.startswith("npm") or
        _peek_cmd.startswith("npx") or
        (exec_dir / "package.json").exists()))
    )
    _is_go = (
        _lang_sel == "go" or (not _lang_sel and (
        "go.mod" in _peek_deps or _peek_cmd.startswith("go ") or
        (exec_dir / "go.mod").exists()))
    )
    _is_ruby = (
        _lang_sel == "ruby" or (not _lang_sel and (
        "Gemfile" in _peek_deps or _peek_cmd.startswith("ruby") or
        _peek_cmd.startswith("bundle") or
        (exec_dir / "Gemfile").exists()))
    )

    # Configurar binarios según el lenguaje
    python_bin = shutil.which("python3") or "python3"
    pip_bin    = shutil.which("pip3") or "pip3"

    if _is_node:
        _lang = "Node.js"
        _node = shutil.which("node") or "node"
        _npm  = shutil.which("npm")  or "npm"
        append_log(pid, f"[nexhost] Lenguaje: {_lang}  ({_node}  npm={_npm})")
    elif _is_go:
        _lang = "Go"
        _go   = shutil.which("go") or "go"
        append_log(pid, f"[nexhost] Lenguaje: {_lang}  ({_go})")
    elif _is_ruby:
        _lang = "Ruby"
        _ruby = shutil.which("ruby") or "ruby"
        append_log(pid, f"[nexhost] Lenguaje: {_lang}  ({_ruby})")
    elif _is_python:
        _lang = "Python"
        append_log(pid, f"[nexhost] Lenguaje: Python {py_ver}")
        old_env_dir = _get_env_dir(pid)
        if old_env_dir.exists():
            shutil.rmtree(str(old_env_dir), ignore_errors=True)
        python_bin, pip_bin = setup_isolated_env(
            pid, py_ver,
            log_fn=lambda m: append_log(pid, m),
            force=True
        )
    else:
        # Fallback: asumir Python si no se detecta nada
        _lang = "Python (auto)"
        append_log(pid, f"[nexhost] Lenguaje: Python {py_ver} (auto)")
        old_env_dir = _get_env_dir(pid)
        if old_env_dir.exists():
            shutil.rmtree(str(old_env_dir), ignore_errors=True)
        python_bin, pip_bin = setup_isolated_env(
            pid, py_ver,
            log_fn=lambda m: append_log(pid, m),
            force=True
        )

    # ── Step 3: Auto-detectar start_cmd ──────────────────────────────────
    if not start_cmd:
        auto_cmd, auto_deps = detect_project_files(exec_dir)
        if auto_cmd:
            start_cmd = auto_cmd
            append_log(pid, f"[nexhost] 🔍 Start command auto-detectado: {start_cmd}")
            projs_d = load_projects()
            for p_d in projs_d:
                if p_d["id"] == pid:
                    p_d["start_cmd"] = start_cmd
                    if not deps_file and auto_deps:
                        p_d["deps_file"] = auto_deps
                        deps_file = auto_deps
            save_projects(projs_d)
        else:
            append_log(pid, "[nexhost] ⚠ Start command no detectado automáticamente")

    # Auto-detectar deps si no se especificó
    if not deps_file:
        _, auto_deps = detect_project_files(exec_dir)
        if auto_deps:
            deps_file = auto_deps
            append_log(pid, f"[nexhost] 🔍 Dependencias auto-detectadas: {deps_file}")

    # ── Step 4: Instalar dependencias ────────────────────────────────────
    append_log(pid, "[nexhost] ── Paso 3: Dependencias")
    if deps_file:
        install_deps_isolated(
            pid, deps_file, str(exec_dir),
            python_bin, pip_bin,
            extra_env={},
            log_fn=lambda m: append_log(pid, m)
        )
    else:
        append_log(pid, "[nexhost] Sin archivo de dependencias — omitido")

    # ── Step 5: Arrancar proceso ──────────────────────────────────────────
    append_log(pid, "[nexhost] ── Paso 4: Arrancar servicio")

    if not start_cmd:
        append_log(pid, "[nexhost] ✕ Sin start command (añádelo en la config)")
        with registry_lock: registry[pid]["status"] = "error"
        _set_project_status(pid, "error"); return

    # Construir entorno de ejecución
    run_env = make_run_env(pid, internal_port, env_vars)
    run_env["PYTHON_BIN"] = python_bin

    # Resolver binario real según lenguaje
    if _is_python or not (_is_node or _is_go or _is_ruby):
        # Autocorrección: 'pythonscript.py' → 'python script.py'
        _fix = re.match(r'^python3?([a-zA-Z_][^\s]*\.py(?:\s.*)?$)', start_cmd)
        if _fix:
            start_cmd = f"python {_fix.group(1)}"
            append_log(pid, f"[nexhost] ⚠ start_cmd corregido: '{start_cmd}'")
        # Sustituir python/python3 por el binario del venv
        if re.match(r'^python3?\s', start_cmd):
            actual_cmd = re.sub(r'^python3?(?=\s)', python_bin, start_cmd)
        elif start_cmd in ('python', 'python3'):
            actual_cmd = python_bin
        else:
            actual_cmd = start_cmd
    elif _is_node:
        # node/npm con PATH que incluye node_modules/.bin
        nm_bin = str(exec_dir / "node_modules" / ".bin")
        run_env["PATH"] = nm_bin + ":" + run_env.get("PATH", os.environ.get("PATH", ""))
        if re.match(r'^node\s', start_cmd) or start_cmd == "node":
            _node_bin = shutil.which("node") or "node"
            actual_cmd = re.sub(r'^node(?=\s|$)', _node_bin, start_cmd)
        elif re.match(r'^npm\s', start_cmd) or start_cmd == "npm":
            _npm_bin = shutil.which("npm") or "npm"
            actual_cmd = re.sub(r'^npm(?=\s|$)', _npm_bin, start_cmd)
        elif re.match(r'^npx\s', start_cmd):
            _npx_bin = shutil.which("npx") or "npx"
            actual_cmd = re.sub(r'^npx(?=\s|$)', _npx_bin, start_cmd)
        else:
            actual_cmd = start_cmd
    elif _is_go:
        _go_bin = shutil.which("go") or "go"
        if re.match(r'^go\s', start_cmd):
            actual_cmd = re.sub(r'^go(?=\s)', _go_bin, start_cmd)
        else:
            actual_cmd = start_cmd
        run_env["GOPATH"] = run_env.get("HOME", "/tmp") + "/go"
    elif _is_ruby:
        _ruby_bin = shutil.which("ruby") or "ruby"
        _bundle   = shutil.which("bundle") or "bundle"
        if re.match(r'^ruby\s', start_cmd):
            actual_cmd = re.sub(r'^ruby(?=\s)', _ruby_bin, start_cmd)
        elif re.match(r'^bundle\s', start_cmd):
            actual_cmd = re.sub(r'^bundle(?=\s)', _bundle, start_cmd)
        else:
            actual_cmd = start_cmd
    else:
        actual_cmd = start_cmd

    append_log(pid, f"[nexhost] $ {actual_cmd}")
    append_log(pid, f"[nexhost] PORT={internal_port} (inyectado)")

    try:
        proc = subprocess.Popen(
            actual_cmd, shell=True, cwd=str(exec_dir),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
            env=run_env, preexec_fn=os.setsid
        )
        with registry_lock:
            registry[pid] = {
                "status": "running", "proc": proc,
                "pid_os": proc.pid, "port": internal_port,
                "python_bin": python_bin,   # necesario para auto-install
            }
        append_log(pid, f"[nexhost] ✓ Proceso arrancado (PID {proc.pid})", "process")
        append_log(pid, f"[nexhost] ✓ Accesible en /p/{pid}", "process")
        _set_project_status(pid, "running")

        _deploy_end = time.time()
        # Guardar commit info en el proyecto para record_deploy
        try:
            project["_last_commit_sha"]    = _git_sha
            project["_last_commit_msg"]    = _git_msg
            project["_last_commit_author"] = _git_author
        except Exception:
            pass
        def check_alive():
            time.sleep(4)
            _no_web = project.get("no_web", False)
            if proc.poll() is not None:
                append_log(pid, f"[nexhost] ⚠ Proceso terminó prematuramente (código {proc.returncode})", "process")
                record_deploy(pid, project, _deploy_end - _deploy_start, False)
            elif _no_web:
                append_log(pid, f"[nexhost] ✓ Proceso sin interfaz web activo (PID {proc.pid}) ✅", "process")
                record_deploy(pid, project, _deploy_end - _deploy_start, True)
                start_healthcheck(pid, internal_port, project)
            elif is_port_open(internal_port):
                append_log(pid, f"[nexhost] ✓ Puerto {internal_port} activo ✅", "process")
                record_deploy(pid, project, _deploy_end - _deploy_start, True)
                start_healthcheck(pid, internal_port, project)
            else:
                append_log(pid, f"[nexhost] ℹ Proceso activo, puerto aún no abierto", "process")
                record_deploy(pid, project, _deploy_end - _deploy_start, True)
                start_healthcheck(pid, internal_port, project)
        threading.Thread(target=check_alive, daemon=True).start()

        # Modo 2: activar watcher de archivos → sube cambios a HF Dataset
        if persist_data:
            start_hf_watcher(pid, project, work_dir)

        # Correr stream_output en su propio hilo para no bloquear start_project
        threading.Thread(target=stream_output, args=(pid, proc), daemon=True).start()

    except Exception as e:
        append_log(pid, f"[nexhost] ✕ Error al arrancar: {e}")
        with registry_lock: registry[pid] = {"status": "error", "proc": None, "port": internal_port}
        _set_project_status(pid, "error")


# ── Auto-detect start command ────────────────────────────────────────────
START_CMD_CANDIDATES = [
    ("Procfile",      None),
    # Python
    ("main.py",       "python main.py"),
    ("app.py",        "python app.py"),
    ("run.py",        "python run.py"),
    ("server.py",     "python server.py"),
    ("bot.py",        "python bot.py"),
    # Node.js
    ("index.js",      "node index.js"),
    ("app.js",        "node app.js"),
    ("server.js",     "node server.js"),
    # TypeScript (ts-node)
    ("index.ts",      "npx ts-node index.ts"),
    ("app.ts",        "npx ts-node app.ts"),
    ("server.ts",     "npx ts-node server.ts"),
    # Deno
    ("main.ts",       "deno run --allow-all main.ts"),
    ("mod.ts",        "deno run --allow-all mod.ts"),
    # Bun
    ("bunfig.toml",   "bun start"),
    # Go
    ("main.go",       "go run main.go"),
    # Rust
    ("Cargo.toml",    "cargo run --release"),
    # Ruby
    ("main.rb",       "ruby main.rb"),
    ("app.rb",        "ruby app.rb"),
    # PHP
    ("index.php",     "php -S 0.0.0.0:8080 index.php"),
    ("server.php",    "php -S 0.0.0.0:8080 server.php"),
    # Java (Maven)
    ("pom.xml",       "mvn spring-boot:run"),
    # Java (Gradle)
    ("build.gradle",  "./gradlew bootRun"),
]
DEPS_CANDIDATES = [
    ("requirements.txt", "requirements.txt"),
    ("pyproject.toml",   "pyproject.toml"),
    ("package.json",     "package.json"),
    ("go.mod",           "go.mod"),
    ("Gemfile",          "Gemfile"),
    ("Cargo.toml",       "Cargo.toml"),
    ("composer.json",    "composer.json"),
    ("pom.xml",          "pom.xml"),
    ("build.gradle",     "build.gradle"),
    ("deno.json",        "deno.json"),
]

def detect_project_files(work_dir):
    work_dir = Path(work_dir)
    start_cmd = ""
    deps_file = ""
    for fname, cmd in START_CMD_CANDIDATES:
        if (work_dir / fname).exists():
            if fname == "Procfile":
                for line in (work_dir / "Procfile").read_text().splitlines():
                    if line.startswith("web:"):
                        start_cmd = line[4:].strip()
                        break
            else:
                start_cmd = cmd
            break
    pkg = work_dir / "package.json"
    if pkg.exists() and not start_cmd:
        try:
            data = json.loads(pkg.read_text())
            if data.get("scripts", {}).get("start"):
                start_cmd = "npm start"
        except: pass
    for fname, label in DEPS_CANDIDATES:
        if (work_dir / fname).exists():
            deps_file = label
            break
    return start_cmd, deps_file


# ── History ───────────────────────────────────────────────────────────────
def record_deploy(pid, project, duration_s, success):
    history_file = DATA_DIR / f"{pid}.history.json"
    history = []
    if history_file.exists():
        try: history = json.loads(history_file.read_text())
        except: pass
    history.append({
        "ts":       datetime.now().isoformat(),
        "duration": round(duration_s, 1),
        "success":  success,
        "branch":   project.get("branch", "main"),
        "cmd":      project.get("start_cmd", ""),
        "python":   project.get("python_version", "3.11"),
        "engine":   "uv" if UV else "pip",
        "commit_sha":    project.get("_last_commit_sha", ""),
        "commit_msg":    project.get("_last_commit_msg", ""),
        "commit_author": project.get("_last_commit_author", ""),
    })
    history = history[-20:]
    history_file.write_text(json.dumps(history))

def get_deploy_history(pid):
    history_file = DATA_DIR / f"{pid}.history.json"
    if not history_file.exists(): return []
    try: return json.loads(history_file.read_text())
    except: return []


# ── Healthcheck ───────────────────────────────────────────────────────────
_healthcheck_threads = {}

def start_healthcheck(pid, port, project, interval=30, max_failures=3):
    no_web = project.get("no_web", False)
    def _watch():
        failures = 0
        time.sleep(15)
        while True:
            time.sleep(interval)
            with registry_lock:
                info = registry.get(pid, {})
                if info.get("status") != "running": break
                proc = info.get("proc")

            if no_web:
                # Proyectos sin web: verificar que el proceso sigue vivo
                if proc and proc.poll() is None:
                    failures = 0  # proceso activo, todo bien
                else:
                    failures += 1
                    append_log(pid, f"[nexhost] ⚠ Proceso sin web terminó inesperadamente ({failures}/{max_failures})", "process")
                    if failures >= max_failures:
                        append_log(pid, "[nexhost] 🔄 Auto-reiniciando proceso sin web...", "process")
                        projs = load_projects()
                        proj  = next((p for p in projs if p["id"] == pid), None)
                        if proj:
                            stop_project(pid)
                            time.sleep(2)
                            threading.Thread(target=start_project, args=(proj,), daemon=True).start()
                        break
            else:
                # Proyectos web: verificar que el puerto responde
                if is_port_open(port):
                    failures = 0
                else:
                    failures += 1
                    append_log(pid, f"[nexhost] ⚠ Healthcheck fallido ({failures}/{max_failures})", "process")
                    if failures >= max_failures:
                        append_log(pid, "[nexhost] 🔄 Auto-reiniciando por healthcheck...", "process")
                        projs = load_projects()
                        proj  = next((p for p in projs if p["id"] == pid), None)
                        if proj:
                            stop_project(pid)
                            time.sleep(2)
                            threading.Thread(target=start_project, args=(proj,), daemon=True).start()
                        break
    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    _healthcheck_threads[pid] = t


def stop_project(pid):
    with registry_lock:
        info = registry.get(pid, {})
        proc = info.get("proc")
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except: proc.terminate()
            registry[pid]["status"] = "stopped"
            registry[pid]["proc"] = None
    append_log(pid, "[nexhost] Detenido por el usuario")
    _set_project_status(pid, "stopped")


def restore_on_boot():
    """
    Restaura automáticamente los proyectos guardados tras un reinicio del host.
    Si el archivo local no existe (Storage no persistente), lo recupera desde HF Dataset.
    Usa backoff exponencial para evitar saturar el sistema en arranque.
    """
    time.sleep(5)  # dar tiempo a que uv, mise y huggingface_hub se inicialicen

    # Si no hay projects.json local, intentar recuperarlo desde HF Dataset
    if not _PROJECTS_FILE.exists():
        print("[nexhost] ⚠ projects.json no encontrado localmente — intentando restaurar desde HF Dataset...")
        _restore_projects_from_hf()

    projs = load_projects()
    # Restaurar proyectos que estaban corriendo O que tienen auto_restart
    # También incluir proyectos sin auto_restart explícito (True por defecto)
    to_restore = [p for p in projs if p.get("auto_restart", True) and
                  p.get("status") in ("running", "error", "building")]
    if not to_restore:
        print(f"[nexhost] ✓ Sin proyectos para restaurar ({len(projs)} guardados)")
        return

    print(f"[nexhost] 🔄 Restaurando {len(to_restore)} proyecto(s) tras reinicio...")

    def _restore_one(proj, delay=0):
        if delay > 0:
            time.sleep(delay)
        pid = proj["id"]
        max_retries = 3
        for attempt in range(max_retries):
            try:
                append_log(pid, f"[nexhost] 🔄 Auto-restaurando tras reinicio (intento {attempt+1}/{max_retries})...")
                _set_project_status(pid, "building")
                start_project(proj, from_boot=True)
                return  # éxito
            except Exception as e:
                wait = (2 ** attempt) * 5  # backoff: 5s, 10s, 20s
                append_log(pid, f"[nexhost] ⚠ Error en intento {attempt+1}: {e}")
                if attempt < max_retries - 1:
                    append_log(pid, f"[nexhost] ⏳ Reintentando en {wait}s...")
                    time.sleep(wait)
                else:
                    append_log(pid, f"[nexhost] ✕ Falló la restauración después de {max_retries} intentos")
                    _set_project_status(pid, "error")

    for i, p in enumerate(to_restore):
        delay = i * 3  # escalonar arranques: más margen entre proyectos
        t = threading.Thread(target=_restore_one, args=(p, delay), daemon=True)
        t.start()


# ── Reverse Proxy ─────────────────────────────────────────────────────────
def proxy_request(handler, internal_port, strip_prefix):
    path     = urlparse(handler.path).path
    # strip_prefix = "/p/<pid>" — eliminar exactamente ese prefijo
    if path.startswith(strip_prefix):
        sub_path = path[len(strip_prefix):]
    else:
        sub_path = path
    # Siempre empezar con "/"
    if not sub_path or not sub_path.startswith("/"):
        sub_path = "/" + (sub_path or "")
    # Preservar query string
    full_path = handler.path
    qs_start = full_path.find("?")
    query_str = full_path[qs_start:] if qs_start != -1 else ""
    target_url = f"http://localhost:{internal_port}{sub_path}{query_str}"

    # URL base canónica del Space (sin sufijo de commit HF)
    canonical_base = _canonical_base(handler)

    try:
        method       = handler.command
        proxy_method = "GET" if method == "HEAD" else method
        length       = int(handler.headers.get("Content-Length", 0))
        body         = handler.rfile.read(length) if length else None
        req          = urllib.request.Request(target_url, data=body, method=proxy_method)
        for k, v in handler.headers.items():
            lk = k.lower()
            if lk in ("host", "connection", "content-length"):
                continue
            try: req.add_header(k, v)
            except: pass
        # Inyectar Host canónico para que el proceso interno sepa su URL real
        req.add_header("Host", canonical_base.split("://", 1)[-1])
        req.add_header("X-Forwarded-Host", canonical_base.split("://", 1)[-1])
        req.add_header("X-Forwarded-Prefix", strip_prefix)
        with urllib.request.urlopen(req, timeout=30) as resp:
            handler.send_response(resp.status)
            for k, v in resp.headers.items():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection"): continue
                if lk == "location":
                    # Reescribir redirects relativos con el prefijo del proxy
                    if v.startswith("/"):
                        v = strip_prefix + v
                    # Reescribir redirects absolutos con URL de commit → URL canónica
                    elif v.startswith("http"):
                        v = re.sub(r'https?://[^/]+-[0-9a-f]{7}\.hf\.space',
                                   canonical_base, v)
                handler.send_header(k, v)
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            handler.wfile.write(resp.read())
    except urllib.error.HTTPError as e:
        handler.send_response(e.code)
        for k, v in e.headers.items():
            lk = k.lower()
            if lk in ("transfer-encoding", "connection"): continue
            if lk == "location":
                if v.startswith("/"):
                    v = strip_prefix + v
                elif v.startswith("http"):
                    v = re.sub(r'https?://[^/]+-[0-9a-f]{7}\.hf\.space',
                               canonical_base, v)
            handler.send_header(k, v)
        handler.end_headers()
        handler.wfile.write(e.read())
    except Exception as e:
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="4">
<title>NexHost — Iniciando</title>
<style>body{{font-family:monospace;background:#050508;color:#e8e8f8;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.b{{text-align:center;padding:32px;border:1px solid #252540;background:#0a0a10;max-width:420px;border-radius:12px}}
h2{{color:#ffc947}}p{{color:#50507a;font-size:12px;line-height:1.6}}
.d{{display:inline-block;width:7px;height:7px;border-radius:50%;background:#ffc947;animation:bl 1s infinite;margin-right:6px}}
@keyframes bl{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}</style></head>
<body><div class="b"><div><span class="d"></span><strong style="color:#00f5a0">NexHost</strong></div>
<h2>Iniciando...</h2>
<p>Puerto interno: {internal_port}<br>El proceso está arrancando.<br>Esta página se recarga cada 4s.</p>
</div></body></html>""".encode()
        handler.send_response(503)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(html)))
        handler.end_headers()
        handler.wfile.write(html)


# ── Helpers ───────────────────────────────────────────────────────────────
def _slugify(name):
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-') or "project"


def _get_mise_version() -> str:
    """Retorna la versión de mise instalada."""
    if not _MISE:
        return ""
    try:
        rv = subprocess.run([_MISE, "--version"], capture_output=True, text=True, timeout=5)
        return rv.stdout.strip().split()[0] if rv.stdout.strip() else ""
    except Exception:
        return ""


# ── HTTP Handler ──────────────────────────────────────────────────────────
STATIC = Path(__file__).parent / "static"
_STATIC_CACHE = {}

def _get_static_file(path):
    if path not in _STATIC_CACHE:
        try:
            _STATIC_CACHE[path] = Path(path).read_bytes()
        except:
            return None
    return _STATIC_CACHE[path]

def _strip_commit_hash(host: str) -> str:
    """Elimina sufijo de hash de commit HF: owner-name-0404c19.hf.space → owner-name.hf.space"""
    return re.sub(r'-[0-9a-f]{6,12}(\.hf\.space)', r'\1', host, flags=re.IGNORECASE)

def _canonical_base(handler) -> str:
    """URL base canónica del entorno, siempre sin hash de commit."""
    proto = "https"
    # 1. Override explícito por env
    explicit = os.environ.get("NEXHOST_DOMAIN", "").strip()
    if explicit:
        if not explicit.startswith("http"):
            explicit = f"{proto}://{explicit}"
        return explicit.rstrip("/")
    # 2. SPACE_ID (Hugging Face) — nunca contiene hash de commit
    space_id = os.environ.get("SPACE_ID", "").strip()
    if space_id and "/" in space_id:
        derived = space_id.replace("/", "-").replace("&", "-").replace("_", "-").lower() + ".hf.space"
        return f"{proto}://{derived}"
    space_host = os.environ.get("SPACE_HOST", "").strip()
    if space_host:
        return f"{proto}://{_strip_commit_hash(space_host)}"
    # 3. Replit
    repl_dev = os.environ.get("REPLIT_DEV_DOMAIN", "").strip()
    if repl_dev:
        return f"{proto}://{repl_dev}"
    repl_url = os.environ.get("REPLIT_URL", "").strip()
    if repl_url:
        if not repl_url.startswith("http"):
            repl_url = f"{proto}://{repl_url}"
        return repl_url.rstrip("/")
    # 4. Headers de proxy
    hdr_space = handler.headers.get("X-Space-Host", "")
    if hdr_space:
        return f"{proto}://{_strip_commit_hash(hdr_space)}"
    fwd_host = handler.headers.get("X-Forwarded-Host", "")
    if fwd_host:
        return f"{proto}://{_strip_commit_hash(fwd_host)}"
    fwd_proto = handler.headers.get("X-Forwarded-Proto", "").strip().lower()
    if fwd_proto in ("http", "https"):
        proto = fwd_proto
    # 5. Último recurso: Host header
    host = handler.headers.get("Host", "")
    if host and ("localhost" in host or host.startswith("127.") or host.startswith("0.0.0.0")):
        proto = "http"
    return f"{proto}://{_strip_commit_hash(host)}"


def _extract_session_token(handler) -> str:
    """Extrae el token de sesión de query, cookie o header. Vacío si no válido."""
    qs = parse_qs(urlparse(handler.path).query)
    qt = qs.get("t", [""])[0]
    if qt and _validate_session_token(qt):
        return qt
    raw = handler.headers.get("Cookie", "")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == "nx_token" and _validate_session_token(v.strip()):
            return v.strip()
    auth_hdr = handler.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        bearer = auth_hdr[7:].strip()
        if _validate_session_token(bearer):
            return bearer
    return ""

def _is_authed(handler):
    return bool(_extract_session_token(handler))

def _current_user(handler):
    """Devuelve el dict del usuario logueado, o None. Para tokens legacy devuelve admin."""
    tok = _extract_session_token(handler)
    if not tok:
        return None
    uid = _session_user_id(tok)
    if not uid:
        return None
    user = get_user(uid)
    if user:
        return user
    # Fallback legacy: tokens admin sin usuario en disco → reconstruir desde admin
    if uid == "admin":
        admin = get_user_by_username("admin")
        return admin
    return None

def _require_admin(handler) -> tuple:
    """Retorna (user, ok). user puede ser None."""
    u = _current_user(handler)
    if not u or not is_admin_user(u):
        return u, False
    return u, True

def _send_login_page(handler):
    """Login page embebida con Cloudflare Turnstile, rate-limit info y diseño mejorado."""
    ip = handler.client_address[0]
    allowed, unlock_in = _check_rate_limit(ip)
    # Info de seguridad para inyectar en el HTML
    locked_msg = ""
    if not allowed:
        mins = unlock_in // 60
        secs = unlock_in % 60
        locked_msg = f"IP bloqueada. Espera {mins}m {secs}s."
    cf_key = CF_SITE_KEY if CF_TURNSTILE_ENABLED else ""
    running = sum(1 for p in load_projects() if p.get("status") == "running")
    total   = len(load_projects())
    _LOGIN_TPL = Path(__file__).parent / "login.html"
    with open(_LOGIN_TPL, "r", encoding="utf-8") as _f:
        _template = _f.read()

    cf_script = ("<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'"
                 " async defer></script>") if cf_key else ""
    cf_widget = (f"<div class='cf-turnstile' data-sitekey='{cf_key}'"
                 " data-theme='dark' style='margin-top:14px'></div>") if cf_key else ""

    html = (_template
            .replace("<!--CF_SCRIPT-->", cf_script)
            .replace("<!--CF_WIDGET-->", cf_widget)
            .replace("{{LOCKED_MSG}}", locked_msg)
            .replace("{{RUNNING}}", str(running))
            .replace("{{TOTAL}}", str(total))
            .replace("{{MAX_ATTEMPTS}}", str(_MAX_ATTEMPTS))
            .replace("{{CF_KEY}}", cf_key)
            .replace("{{GOOGLE_ENABLED}}", "1" if GOOGLE_OAUTH_ENABLED else "")
            .replace("{{GOOGLE_CLIENT_ID}}", GOOGLE_CLIENT_ID))
    data = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store, no-cache")
    handler.end_headers()
    handler.wfile.write(data)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers(); self.wfile.write(body)

    def send_file(self, path, ct):
        data = _get_static_file(path)
        if data:
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        else:
            self.send_response(404); self.end_headers()

    def _oauth_redirect_with_error(self, msg: str):
        """Redirige a /login con un mensaje de error tras fallo de OAuth."""
        from urllib.parse import quote
        self.send_response(302)
        self.send_header("Location", f"/login?oauth_error={quote(msg)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_OPTIONS(self): self.send_json({})

    def do_HEAD(self):
        # HEAD request — UptimeRobot y monitores suelen usarlo
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()

    def _proxy_if_project(self):
        path = urlparse(self.path).path
        if path.startswith("/p/"):
            parts = path[3:].split("/", 1)
            pid = parts[0]
            with registry_lock:
                reg_info = registry.get(pid, {})
                port = reg_info.get("port")
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if not port and proj:
                port = proj.get("internal_port")

            # ── Proyecto sin interfaz web ─────────────────────────────────
            if proj and proj.get("no_web", False):
                with registry_lock:
                    info = registry.get(pid, {})
                proc  = info.get("proc")
                alive = proc and proc.poll() is None
                status       = "RUNNING" if alive else info.get("status", "stopped").upper()
                status_color = "#00f5a0" if alive else "#ff6b6b"
                status_icon  = "✅" if alive else "🔴"
                html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>NexHost — {proj.get('name','Proyecto')}</title>
<style>
  body{{font-family:monospace;background:#050508;color:#e8e8f8;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
  .box{{text-align:center;padding:36px 40px;border:1px solid #252540;
        background:#0a0a10;max-width:480px;border-radius:14px}}
  h2{{color:#ffc947;margin:12px 0 6px}}
  .badge{{display:inline-block;padding:4px 14px;border-radius:20px;
          background:{status_color}22;color:{status_color};
          border:1px solid {status_color}55;font-size:13px;font-weight:bold;
          letter-spacing:.5px;margin-bottom:16px}}
  p{{color:#50507a;font-size:13px;line-height:1.8;margin:0}}
  .pid{{color:#252540;font-size:11px;margin-top:14px}}
</style></head>
<body><div class="box">
  <div style="font-size:22px">📦</div>
  <h2>{proj.get('name','Proyecto')}</h2>
  <div class="badge">{status_icon} {status}</div>
  <p>Este proyecto no expone una interfaz web.<br>
     Se ejecuta en background como proceso de fondo.<br>
     Revisa los logs en el panel de NexHost para ver su actividad.</p>
  <p class="pid">id: {pid}</p>
</div></body></html>""".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return True

            # ── Proyecto web normal ───────────────────────────────────────
            if port:
                # Redirigir /p/<pid> → /p/<pid>/ usando el host canónico
                # para evitar que HF sirva la URL con sufijo de commit (-0404c19)
                if path == f"/p/{pid}":
                    base = _canonical_base(self)
                    self.send_response(301)
                    self.send_header("Location", f"{base}/p/{pid}/")
                    self.end_headers()
                    return True
                proxy_request(self, port, f"/p/{pid}")
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Project not found or not running")
            return True
        return False

    def do_GET(self):
        # Bypass /ping — antes de auth, proxy y cualquier otra lógica
        if self.path.endswith('/ping'):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'pong')
            return

        # ── Redirect global: eliminar hash de commit HF (-XXXXXXX.hf.space) ─
        _req_host = self.headers.get("Host", "")
        if re.search(r'-[0-9a-f]{6,12}\.hf\.space$', _req_host, re.IGNORECASE) \
                and os.environ.get("SPACE_ID", ""):
            _clean_base = _canonical_base(self)
            _pp = urlparse(self.path)
            _loc = f"{_clean_base}{_pp.path}" + (f"?{_pp.query}" if _pp.query else "")
            self.send_response(301)
            self.send_header("Location", _loc)
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            return

        # Root path: serve app if authed, login page if not
        if urlparse(self.path).path in ('/', ''):
            if _is_authed(self):
                return self.send_file(STATIC / "index.html", "text/html; charset=utf-8")
            return _send_login_page(self)

        if self._proxy_if_project(): return
        path = urlparse(self.path).path
        qs   = parse_qs(urlparse(self.path).query)

        if path == "/health":
            projs = load_projects()
            return self.send_json({
                "status": "ok",
                "projects": len(projs),
                "running": sum(1 for p in projs if p.get("status") == "running"),
                "storage": str(PERSIST),
                "engine": "uv" if UV else "pip",
                "mise": bool(_MISE),
                "mise_path": str(_MISE) if _MISE else None,
                "envs_base": str(ENVS_BASE),
            })

        if path == "/api/audit-log":
            if not _is_authed(self): return self.send_json({"error":"No autorizado"},401)
            limit = int(qs.get("limit",["100"])[0])
            with _AUDIT_LOCK:
                logs = list(reversed(_AUDIT_LOG[-limit:]))
            return self.send_json({"logs": logs, "total": len(_AUDIT_LOG)})

        if path == "/api/security-status":
            if not _is_authed(self): return self.send_json({"error":"No autorizado"},401)
            now = time.time()
            with _ip_lock:
                blocked = {
                    ip: int(_LOCKOUT_TIME - (now - attempts[0]))
                    for ip, attempts in _ip_attempts.items()
                    if len(attempts) >= _MAX_ATTEMPTS
                    and (now - attempts[0]) < _LOCKOUT_TIME
                }
            with _AUDIT_LOCK:
                recent = _AUDIT_LOG[-20:]
                fails_last_hour = sum(
                    1 for e in _AUDIT_LOG
                    if not e["ok"]
                    and (datetime.fromisoformat(e["ts"]) > datetime.now().replace(microsecond=0)
                         if True else True)
                )
            return self.send_json({
                "turnstile_enabled": CF_TURNSTILE_ENABLED,
                "max_attempts": _MAX_ATTEMPTS,
                "lockout_seconds": _LOCKOUT_TIME,
                "blocked_ips": blocked,
                "active_sessions": len(_active_tokens),
                "total_audit_entries": len(_AUDIT_LOG),
                "recent_logins": recent,
            })


            # FIXED: Revocar el token de TODAS las fuentes (cookie, query param y header)
            # Antes solo se revocaba el token de la Cookie, dejando activo el token
            # almacenado en localStorage (enviado via ?t=) tras el logout.
            qs_logout = parse_qs(urlparse(self.path).query)
            qt_logout = qs_logout.get("t", [""])[0]
            if qt_logout:
                _revoke_session_token(qt_logout)
            raw_cookie = self.headers.get("Cookie", "")
            for part in raw_cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k.strip() == "nx_token":
                    _revoke_session_token(v.strip())
            auth_hdr_logout = self.headers.get("Authorization", "")
            if auth_hdr_logout.startswith("Bearer "):
                _revoke_session_token(auth_hdr_logout[7:].strip())
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "nx_token=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if path == "/api/token-refresh":
            if not _is_authed(self):
                return self.send_json({"error": "No autorizado"}, 401)
            new_token = _generate_session_token()
            resp = json.dumps({"ok": True, "token": new_token, "expires_in": _TOKEN_TTL}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.send_header("Set-Cookie",
                f"nx_token={new_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_TOKEN_TTL}")
            self.end_headers()
            self.wfile.write(resp)
            return

        if path == "/login": return _send_login_page(self)

        # ── Endpoints públicos: OAuth Google (no requieren sesión) ─────────
        if path in ("/api/google/start", "/api/google/callback"):
            # Continuar — la lógica de OAuth se ejecuta más abajo
            pass
        elif not _is_authed(self):
            if path.startswith("/api/"):
                return self.send_json({"error": "No autorizado"}, 401)
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers(); return

        # GitHub API proxy
        if path.startswith("/api/gh/"):
            cur = _current_user(self)
            # Token: primero el del usuario, luego el global (sólo admin)
            token = (cur.get("gh_token", "") if cur else "") or ""
            if not token and is_admin_user(cur):
                token = load_config().get("gh_token", "")
            if not token:
                return self.send_json({"error": "No hay token de GitHub configurado",
                                       "needs_gh_token": True}, 400)
            gh_path = path[len("/api/gh"):]
            # Filtrar el parámetro 't' (auth de NexHost) antes de reenviar a GitHub
            raw_qs  = urlparse(self.path).query
            clean_qs = "&".join(
                part for part in raw_qs.split("&")
                if part and not part.startswith("t=")
            )
            gh_url  = f"https://api.github.com{gh_path}" + (f"?{clean_qs}" if clean_qs else "")
            try:
                req = urllib.request.Request(gh_url, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "NexHost/3.0"
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers(); self.wfile.write(data)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(e.read())
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if path == "/api/projects":
            cur = _current_user(self)
            projs_all = load_projects()
            # Filtrar por dueño (admin ve todo)
            if is_admin_user(cur):
                projs = projs_all
            else:
                uid = cur.get("id") if cur else ""
                projs = [p for p in projs_all if p.get("owner_id") == uid]
            # Auto-detectar dominio base canónico (elimina sufijo de commit HF)
            _env_domain = os.environ.get("NEXHOST_DOMAIN", "") or _canonical_base(self)
            base_domain = _env_domain
            for p in projs:
                with registry_lock: info = registry.get(p["id"], {})
                live = info.get("proc")
                if live and live.poll() is None: p["status"] = "running"
                elif p.get("status") == "running": p["status"] = "stopped"
                p["internal_port"] = info.get("port") or p.get("internal_port")
                p["proxy_url"] = f"/p/{p['id']}"
                if p.get("no_web", False):
                    # Sin web: la URL lleva a la página de estado, no a un servidor real
                    p["public_url"] = f"{base_domain.rstrip('/')}/p/{p['id']}" if base_domain else f"/p/{p['id']}"
                    p["has_web"] = False
                elif p.get("custom_domain"):
                    p["public_url"] = f"https://{p['custom_domain']}"
                    p["has_web"] = True
                elif base_domain:
                    _bd = base_domain.rstrip("/")
                    if not _bd.startswith("http"):
                        _bd = f"https://{_bd}"
                    p["public_url"] = f"{_bd}/p/{p['id']}"
                    p["has_web"] = True
                else:
                    p["public_url"] = f"/p/{p['id']}"
                    p["has_web"] = True
                # Info del motor
                p["engine"] = "uv" if UV else "pip"
                env_dir = _get_env_dir(p["id"])
                p["env_exists"] = env_dir.exists()
                # files_ready: True solo en Modo Local cuando el clone/download completó
                if p.get("persist_data"):
                    work_dir_check = BASE_DIR / p["id"]
                    p["files_ready"] = (work_dir_check.exists() and
                                        any(f for f in work_dir_check.iterdir()
                                            if f.name != ".git")
                                        if work_dir_check.exists() else False)
                else:
                    p["files_ready"] = False  # Modo Espejo: nunca accesible
            return self.send_json(projs)

        if path == "/api/logs":
            log_type = qs.get("type", ["build"])[0]
            return self.send_json({"logs": get_logs(
                qs.get("id", [""])[0],
                int(qs.get("lines", ["300"])[0]),
                log_type
            )})

        if path == "/api/history":
            pid = qs.get("id", [""])[0]
            # ?clear=1 borra todo el historial
            if qs.get("clear", [""])[0] == "1":
                hf = DATA_DIR / f"{pid}.history.json"
                if hf.exists(): hf.write_text("[]")
                return self.send_json({"ok": True})
            # ?remove=INDEX borra un deploy específico (índice desde el más reciente)
            remove_idx = qs.get("remove", [""])[0]
            if remove_idx.isdigit():
                hf = DATA_DIR / f"{pid}.history.json"
                hist = []
                if hf.exists():
                    try: hist = json.loads(hf.read_text())
                    except: pass
                idx = int(remove_idx)
                # El historial se muestra invertido — convertir índice visual a real
                real_idx = len(hist) - 1 - idx
                if 0 <= real_idx < len(hist):
                    hist.pop(real_idx)
                    hf.write_text(json.dumps(hist))
                return self.send_json({"ok": True})
            return self.send_json({"history": get_deploy_history(pid)})

        if path == "/api/stats":
            pid = qs.get("id", [""])[0]
            with registry_lock: info = registry.get(pid, {})
            proc  = info.get("proc")
            port  = info.get("port")
            alive = proc and proc.poll() is None
            mem_mb = 0; cpu_pct = 0
            if alive and proc:
                try:
                    import resource
                    r = resource.getrusage(resource.RUSAGE_CHILDREN)
                    mem_mb = round(r.ru_maxrss / 1024, 1)
                except: pass
            env_dir   = _get_env_dir(pid)
            data_dir  = PROJECT_DATA_BASE / pid
            env_size  = 0
            data_size = 0
            if env_dir.exists():
                try:
                    env_size = sum(f.stat().st_size for f in env_dir.rglob("*") if f.is_file()) // 1024 // 1024
                except: pass
            if data_dir.exists():
                try:
                    data_size = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file()) // 1024 // 1024
                except: pass
            return self.send_json({
                "alive": alive, "port": port,
                "mem_mb": mem_mb, "cpu_pct": cpu_pct,
                "engine": "uv" if UV else "pip",
                "env_dir": str(env_dir),
                "env_size_mb": env_size,
                "data_dir": str(data_dir),
                "data_size_mb": data_size,
            })

        if path == "/api/files":
            pid = qs.get("id", [""])[0]
            work_dir = BASE_DIR / pid
            if not work_dir.exists():
                return self.send_json({"files": []})
            # Modo Espejo: bloquear acceso al explorador de archivos
            projs_check = load_projects()
            proj_check = next((p for p in projs_check if p["id"] == pid), None)
            if proj_check and not proj_check.get("persist_data", False):
                return self.send_json({"error": "Modo Espejo: acceso a archivos no disponible", "files": []})
            SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".uv"}
            SKIP_EXTS = {".pyc", ".pyo"}
            result = []
            for f in sorted(work_dir.rglob("*")):
                if not f.is_file(): continue
                if any(p in SKIP_DIRS for p in f.parts): continue
                if f.suffix.lower() in SKIP_EXTS: continue
                rel = str(f.relative_to(work_dir))
                result.append({"path": rel, "size": f.stat().st_size, "ext": f.suffix.lower()})
            return self.send_json({"files": result})

        if path == "/api/file-read":
            pid  = qs.get("id", [""])[0]
            frel = qs.get("path", [""])[0]
            work_dir = BASE_DIR / pid
            target = (work_dir / frel).resolve()
            if not str(target).startswith(str(work_dir.resolve())):
                return self.send_json({"error": "Acceso denegado"}, 403)
            if not target.exists():
                return self.send_json({"error": "Archivo no encontrado"}, 404)
            TEXT_EXTS = {".py",".js",".ts",".html",".css",".json",".txt",".md",
                         ".yaml",".yml",".toml",".ini",".cfg",".sh",".env",
                         ".xml",".csv",".rs",".go",".rb",".php",".java",".c",
                         ".cpp",".h",".tf",".Dockerfile",""}
            if target.suffix.lower() in TEXT_EXTS or target.stat().st_size < 500_000:
                try:
                    file_text = target.read_text(errors="replace")
                    return self.send_json({"content": file_text, "binary": False})
                except Exception:
                    pass
            return self.send_json({"content": "", "binary": True})

        if path == "/api/config":
            cur     = _current_user(self)
            is_adm  = is_admin_user(cur)
            env_gh  = os.environ.get("GH_TOKEN", "")
            user_gh = (cur or {}).get("gh_token", "")
            # Token GH efectivo: el del usuario, o el del env si es admin
            effective_gh = user_gh or (env_gh if is_adm else "")
            hf  = os.environ.get("BOT_DATA_TOKEN", "")
            dom = _canonical_base(self)
            return self.send_json({
                "has_gh_token":     bool(effective_gh),
                "gh_token_preview": ("***" + effective_gh[-4:]) if effective_gh else "",
                "gh_token_from_env": is_adm and bool(env_gh) and not user_gh,
                "gh_token_from_user": bool(user_gh),
                "hf_token_from_env": bool(hf),
                "storage_path": str(PERSIST),
                "base_domain":  dom,
                "engine":       "uv" if UV else "pip",
                "mise":         bool(_MISE),
                "mise_version": _get_mise_version(),
                "envs_base":    str(ENVS_BASE),
                "session_ttl":  _TOKEN_TTL,
                "user": {
                    "id":       cur.get("id", "") if cur else "",
                    "username": cur.get("username", "") if cur else "",
                    "email":    cur.get("email", "") if cur else "",
                    "name":     cur.get("name", "") if cur else "",
                    "picture":  cur.get("picture", "") if cur else "",
                    "role":     cur.get("role", "user") if cur else "user",
                    "notify_errors": cur.get("notify_errors", True) if cur else True,
                } if cur else None,
                "is_admin":      is_adm,
                "google_oauth":  GOOGLE_OAUTH_ENABLED,
                "sendgrid":      SENDGRID_ENABLED,
            })

        # ── Datos del usuario actual ────────────────────────────────────────
        if path == "/api/me":
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            safe = {k: v for k, v in cur.items() if k != "password_hash"}
            safe["has_gh_token"] = bool(cur.get("gh_token"))
            safe["gh_token_preview"] = ("***" + cur["gh_token"][-4:]) if cur.get("gh_token") else ""
            return self.send_json(safe)

        # ── Estadísticas globales (sólo admin) ──────────────────────────────
        if path == "/api/admin/stats":
            cur, ok = _require_admin(self)
            if not ok:
                return self.send_json({"error": "Sólo admin"}, 403)
            users = load_users()
            projs = load_projects()
            now = time.time()
            running_now = 0
            for p in projs:
                with registry_lock:
                    info = registry.get(p["id"], {})
                live = info.get("proc")
                if live and live.poll() is None:
                    running_now += 1
            with_email   = sum(1 for u in users if (u.get("email") or "").strip() and "@" in (u.get("email") or ""))
            active_30d   = sum(1 for u in users if (now - (u.get("last_login", 0) or 0)) < 30 * 86400)
            banned_perm  = sum(1 for u in users if u.get("banned_until", 0) == -1)
            banned_temp  = sum(1 for u in users if (u.get("banned_until", 0) or 0) > now)
            with_gh_tok  = sum(1 for u in users if u.get("gh_token"))
            return self.send_json({
                "users": {
                    "total":       len(users),
                    "with_email":  with_email,
                    "active_30d":  active_30d,
                    "banned_perm": banned_perm,
                    "banned_temp": banned_temp,
                    "with_gh_token": with_gh_tok,
                },
                "projects": {
                    "total":   len(projs),
                    "running": running_now,
                    "stopped": len(projs) - running_now,
                },
                "services": {
                    "sendgrid":   SENDGRID_ENABLED,
                    "google_oauth": GOOGLE_OAUTH_ENABLED,
                    "turnstile":  CF_TURNSTILE_ENABLED,
                    "engine":     "uv" if UV else "pip",
                    "mise":       bool(_MISE),
                },
                "server_time": int(now),
            })

        if path == "/api/admin/users":
            cur, ok = _require_admin(self)
            if not ok:
                return self.send_json({"error": "Sólo admin"}, 403)
            users = load_users()
            projs = load_projects()
            now = time.time()
            out = []
            for u in users:
                pcount = sum(1 for p in projs if p.get("owner_id") == u.get("id"))
                running = sum(1 for p in projs if p.get("owner_id") == u.get("id")
                              and p.get("status") == "running")
                bu = u.get("banned_until", 0)
                if bu == -1:
                    ban = "perm"
                elif bu and bu > now:
                    ban = f"temp:{int(bu - now)}"
                else:
                    ban = ""
                out.append({
                    "id":           u.get("id"),
                    "username":     u.get("username"),
                    "email":        u.get("email", ""),
                    "name":         u.get("name", ""),
                    "picture":      u.get("picture", ""),
                    "role":         u.get("role", "user"),
                    "google_sub":   bool(u.get("google_sub")),
                    "has_gh_token": bool(u.get("gh_token")),
                    "created_at":   u.get("created_at", 0),
                    "last_login":   u.get("last_login", 0),
                    "ban":          ban,
                    "ban_reason":   u.get("ban_reason", ""),
                    "ban_by":       u.get("ban_by", ""),
                    "ban_at":       u.get("ban_at", 0),
                    "projects":     pcount,
                    "running":      running,
                })
            return self.send_json({"users": out, "total": len(out)})

        # ── Inicio del flujo OAuth Google ──────────────────────────────────
        if path == "/api/google/start":
            if not GOOGLE_OAUTH_ENABLED:
                return self.send_json({"error": "Google OAuth no configurado"}, 400)
            redirect_uri = _oauth_redirect_uri(self)
            state = _new_oauth_state(qs.get("redirect", ["/"])[0])
            params = _urllib_parse.urlencode({
                "client_id":     GOOGLE_CLIENT_ID,
                "response_type": "code",
                "scope":         "openid email profile",
                "redirect_uri":  redirect_uri,
                "state":         state,
                "access_type":   "online",
                "prompt":        "select_account",
            })
            url = f"{GOOGLE_AUTH_URL}?{params}"
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        # ── Callback OAuth Google ──────────────────────────────────────────
        if path == "/api/google/callback":
            if not GOOGLE_OAUTH_ENABLED:
                return self.send_json({"error": "Google OAuth no configurado"}, 400)
            code  = qs.get("code", [""])[0]
            state = qs.get("state", [""])[0]
            err   = qs.get("error", [""])[0]
            if err:
                return self._oauth_redirect_with_error(f"Google: {err}")
            if not code or not state or not _consume_oauth_state(state):
                return self._oauth_redirect_with_error("Sesión OAuth inválida")
            try:
                tokens = google_exchange_code(code, _oauth_redirect_uri(self))
                access_token = tokens.get("access_token", "")
                if not access_token:
                    return self._oauth_redirect_with_error("Google no devolvió token")
                info = google_userinfo(access_token)
            except Exception as e:
                return self._oauth_redirect_with_error(f"Error Google: {e}")
            sub     = info.get("sub", "")
            email   = info.get("email", "")
            name    = info.get("name", "")
            picture = info.get("picture", "")
            if not sub:
                return self._oauth_redirect_with_error("Google: respuesta sin sub")

            # Buscar/crear usuario
            user = get_user_by_google_sub(sub)
            if not user and email:
                # Vincular cuenta existente con mismo email
                existing = get_user_by_email(email)
                if existing:
                    update_user(existing["id"], google_sub=sub,
                                picture=picture or existing.get("picture", ""),
                                name=name or existing.get("name", ""))
                    user = get_user(existing["id"])
            if not user:
                # Crear usuario nuevo
                base_username = (email.split("@")[0] if email else f"user_{sub[:8]}")
                base_username = re.sub(r'[^a-zA-Z0-9_.-]', '', base_username) or f"user_{sub[:8]}"
                username = base_username
                k = 1
                while get_user_by_username(username):
                    k += 1
                    username = f"{base_username}{k}"
                user, err = create_user(
                    username=username,
                    email=email,
                    google_sub=sub,
                    name=name,
                    picture=picture,
                    role="user",
                )
                if not user:
                    return self._oauth_redirect_with_error(f"No se pudo crear usuario: {err}")

            # Verificar baneo
            banned, ban_msg = is_banned(user)
            if banned:
                return self._oauth_redirect_with_error(ban_msg)

            update_user(user["id"], last_login=int(time.time()))
            session_token = _generate_session_token(user["id"])
            _audit(self.client_address[0], True, f"google:{user['username']}",
                   self.headers.get("User-Agent", ""))
            self.send_response(302)
            self.send_header("Location", f"/?t={session_token}")
            self.send_header("Set-Cookie",
                f"nx_token={session_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_TOKEN_TTL}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        if path == "/api/detect":
            return self.send_json({"error": "Use POST"}, 405)

        if path in ("/", "/index.html"):
            return self.send_file(STATIC / "index.html", "text/html")

        # Static files
        static_map = {
            "/favicon.ico": ("image/x-icon", STATIC / "favicon.ico"),
        }
        if path in static_map:
            ct, fp = static_map[path]
            return self.send_file(fp, ct)

        # FIXED: /api/file-save y /api/file-delete son endpoints POST exclusivamente.
        # Antes estaban duplicados aquí en do_GET usando 'body' (indefinido en GET),
        # lo que causaba NameError en cualquier GET a estas rutas.
        if path in ("/api/file-save", "/api/file-delete"):
            return self.send_json({"error": "Método no permitido — usar POST"}, 405)

        self.send_response(404); self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        # /api/login es el único endpoint público — debe estar ANTES del guard
        if path == "/api/login":
            length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(body_raw) if body_raw else {}
            except:
                body = {}

            client_ip = self.client_address[0]
            ua = self.headers.get("User-Agent", "")

            # ── Rate limit check ────────────────────────────────────────────
            allowed, unlock_in = _check_rate_limit(client_ip)
            if not allowed:
                mins = unlock_in // 60
                secs = unlock_in % 60
                _audit(client_ip, False, "rate_limited", ua)
                time.sleep(1)
                return self.send_json({
                    "ok": False,
                    "error": f"Demasiados intentos fallidos. Espera {mins}m {secs}s.",
                    "locked": True,
                    "unlock_in": unlock_in,
                }, 429)

            pwd      = body.get("password", "")
            cf_token = body.get("cf_token", "")

            # ── Cloudflare Turnstile verification ───────────────────────────
            if CF_TURNSTILE_ENABLED:
                ts_ok, ts_reason = _verify_turnstile(cf_token, client_ip)
                if not ts_ok:
                    _audit(client_ip, False, f"turnstile:{ts_reason}", ua)
                    return self.send_json({
                        "ok": False,
                        "captcha": True,
                        "error": "Verificación anti-bot fallida. Recarga la página e inténtalo de nuevo.",
                    }, 403)

            # ── Identificación: email, username o (legacy) sólo password ────
            identifier = (body.get("identifier") or body.get("email")
                          or body.get("username") or "").strip()

            user = None
            if identifier:
                if "@" in identifier:
                    user = get_user_by_email(identifier)
                else:
                    user = get_user_by_username(identifier)

            # Compatibilidad: si no se mandó identifier y la contraseña coincide
            # con el secret legacy, login como admin
            legacy_match = (not identifier and pwd and
                            hashlib.sha256(pwd.encode()).hexdigest() == AUTH_TOKEN)
            if legacy_match:
                user = get_user_by_username("admin") or user

            valid = False
            if user and user.get("password_hash"):
                valid = _verify_password(pwd, user["password_hash"])
            elif legacy_match and user:
                valid = True

            if not valid:
                _record_attempt(client_ip)
                remaining_attempts = max(0, _MAX_ATTEMPTS - len(_ip_attempts.get(client_ip, [])))
                _audit(client_ip, False, "wrong_credentials", ua)
                time.sleep(0.6)
                msg = "Credenciales incorrectas"
                if remaining_attempts == 0:
                    msg = f"IP bloqueada por {_LOCKOUT_TIME//60} min."
                elif remaining_attempts <= 2:
                    msg = f"Credenciales incorrectas. {remaining_attempts} intento{'s' if remaining_attempts!=1 else ''} restante{'s' if remaining_attempts!=1 else ''}."
                return self.send_json({"ok": False, "error": msg, "attempts_left": remaining_attempts}, 401)

            # ── Verificar baneo ─────────────────────────────────────────────
            banned, ban_msg = is_banned(user)
            if banned:
                _audit(client_ip, False, f"banned:{user.get('username','')}", ua)
                return self.send_json({"ok": False, "banned": True, "error": ban_msg}, 403)

            # ── OK: emitir sesión ───────────────────────────────────────────
            _clear_attempts(client_ip)
            _audit(client_ip, True, f"ok:{user.get('username','')}", ua)
            update_user(user["id"], last_login=int(time.time()))
            session_token = _generate_session_token(user["id"])
            resp_data = json.dumps({
                "ok": True,
                "token": session_token,
                "expires_in": _TOKEN_TTL,
                "engine": "uv" if UV else "pip",
                "mise": bool(_MISE),
                "user": {
                    "id":       user["id"],
                    "username": user["username"],
                    "email":    user.get("email", ""),
                    "name":     user.get("name", ""),
                    "picture":  user.get("picture", ""),
                    "role":     user.get("role", "user"),
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_data)))
            self.send_header("Set-Cookie",
                f"nx_token={session_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_TOKEN_TTL}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_data)
            return

        # ── REGISTRO de usuario nuevo (público) ─────────────────────────────
        if path == "/api/register":
            length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(body_raw) if body_raw else {}
            except:
                body = {}
            client_ip = self.client_address[0]
            ua = self.headers.get("User-Agent", "")

            allowed, unlock_in = _check_rate_limit(client_ip)
            if not allowed:
                return self.send_json({"ok": False, "error": "Demasiados intentos, espera"}, 429)

            cf_token = body.get("cf_token", "")
            if CF_TURNSTILE_ENABLED:
                ts_ok, ts_reason = _verify_turnstile(cf_token, client_ip)
                if not ts_ok:
                    return self.send_json({"ok": False, "captcha": True,
                                           "error": "Verificación anti-bot fallida"}, 403)

            user, err = create_user(
                username=body.get("username", ""),
                email=body.get("email", ""),
                password=body.get("password", ""),
                role="user",
            )
            if not user:
                _audit(client_ip, False, f"register_fail:{err}", ua)
                return self.send_json({"ok": False, "error": err}, 400)

            _clear_attempts(client_ip)
            _audit(client_ip, True, f"register:{user['username']}", ua)
            session_token = _generate_session_token(user["id"])
            resp_data = json.dumps({
                "ok": True,
                "token": session_token,
                "expires_in": _TOKEN_TTL,
                "user": {
                    "id":       user["id"],
                    "username": user["username"],
                    "email":    user.get("email", ""),
                    "name":     user.get("name", ""),
                    "role":     user.get("role", "user"),
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_data)))
            self.send_header("Set-Cookie",
                f"nx_token={session_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_TOKEN_TTL}")
            self.end_headers()
            self.wfile.write(resp_data)
            return

        # A partir de aquí todos los endpoints requieren auth
        if not _is_authed(self):
            return self.send_json({"error": "No autorizado"}, 401)
        length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(body_raw) if body_raw else {}
        except:
            body = {}

        if path == "/api/deploy":
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            project = body
            pid = project.get("id") or "p_" + str(int(time.time()))
            project["id"] = pid
            projs = load_projects()
            existing = next((p for p in projs if p["id"] == pid), None)
            # Si es un edit/redeploy, verificar dueño
            if existing and not project_belongs_to(existing, cur):
                return self.send_json({"error": "No eres el dueño de este proyecto"}, 403)
            # Asignar dueño (preservar el original si existe)
            project["owner_id"] = (existing.get("owner_id") if existing else None) or cur["id"]
            projs = [p for p in projs if p["id"] != pid]
            project["status"] = "building"
            # Si el usuario no es admin y no envió token, usar el suyo (no env)
            if not project.get("gh_token"):
                project["gh_token"] = cur.get("gh_token", "") or (
                    load_config().get("gh_token", "") if is_admin_user(cur) else "")
            projs.append(project)
            save_projects(projs)
            # Auto-instalar webhook en GitHub para redeploy automático al hacer push
            _repo  = project.get("repo_url", "")
            _tok   = project.get("gh_token", "")
            if _repo and _tok and "github.com" in _repo:
                _base  = _canonical_base(self)
                def _do_wh(_r=_repo, _t=_tok, _b=_base, _pid=pid):
                    res = _install_github_webhook(_r, _t, _b)
                    if res == "already_exists":
                        append_log(_pid, "[nexhost] ⚡ Webhook GitHub ya activo — redeploy automático activado")
                    elif res:
                        append_log(_pid, f"[nexhost] ⚡ Webhook GitHub instalado — redeploy automático activado")
                threading.Thread(target=_do_wh, daemon=True).start()
            t = threading.Thread(target=start_project, args=(project,), daemon=True)
            t.start()
            return self.send_json({"ok": True, "id": pid})

        if path == "/api/upload-project":
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            # ── Deploy de proyecto personalizado sin GitHub (ZIP en base64) ──
            project   = {k: v for k, v in body.items() if k != "files_b64"}
            files_b64 = body.get("files_b64", "")
            pid = project.get("id") or "p_" + str(int(time.time() * 1000))
            project["id"]       = pid
            project["owner_id"] = cur["id"]
            project["source"]   = "upload"
            project["repo_url"] = project.get("repo_url", "")
            project["status"]   = "building"
            work_dir = BASE_DIR / pid
            work_dir.mkdir(parents=True, exist_ok=True)
            if files_b64:
                try:
                    zip_bytes = base64.b64decode(files_b64)
                    with zipfile.ZipFile(_io_mod.BytesIO(zip_bytes)) as zf:
                        zf.extractall(work_dir)
                    # Aplanar si hay una sola carpeta raíz
                    items = [x for x in work_dir.iterdir()]
                    if len(items) == 1 and items[0].is_dir():
                        sub = items[0]
                        for f in list(sub.iterdir()):
                            shutil.move(str(f), str(work_dir / f.name))
                        sub.rmdir()
                    append_log(pid, f"[nexhost] ✓ ZIP extraído en {work_dir}")
                except Exception as e:
                    append_log(pid, f"[nexhost] ✕ Error extrayendo ZIP: {e}")
                    return self.send_json({"ok": False, "error": str(e)}, 400)
            projs = load_projects()
            projs = [p for p in projs if p["id"] != pid]
            projs.append(project)
            save_projects(projs)
            threading.Thread(target=start_project, args=(project,), daemon=True).start()
            return self.send_json({"ok": True, "id": pid})

        if path == "/api/upload-project-files":
            # ── Reemplazar archivos de un proyecto subido previamente ────────
            pid       = body.get("id", "")
            files_b64 = body.get("files_b64", "")
            if not pid or not files_b64:
                return self.send_json({"ok": False, "error": "Faltan id o files_b64"}, 400)
            work_dir = BASE_DIR / pid
            if work_dir.exists():
                for item in work_dir.iterdir():
                    if item.name == ".git": continue
                    shutil.rmtree(str(item)) if item.is_dir() else item.unlink()
            work_dir.mkdir(parents=True, exist_ok=True)
            try:
                zip_bytes = base64.b64decode(files_b64)
                with zipfile.ZipFile(_io_mod.BytesIO(zip_bytes)) as zf:
                    zf.extractall(work_dir)
                items = [x for x in work_dir.iterdir() if x.name != ".git"]
                if len(items) == 1 and items[0].is_dir():
                    sub = items[0]
                    for f in list(sub.iterdir()):
                        shutil.move(str(f), str(work_dir / f.name))
                    sub.rmdir()
                append_log(pid, "[nexhost] ✓ Archivos actualizados correctamente")
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, 400)
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if proj:
                stop_project(pid)
                time.sleep(0.5)
                threading.Thread(target=start_project, args=(proj,), daemon=True).start()
            return self.send_json({"ok": True})

        if path == "/api/stop":
            cur = _current_user(self)
            pid = body.get("id", "")
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if proj and not project_belongs_to(proj, cur):
                return self.send_json({"error": "Sin permiso"}, 403)
            stop_project(pid)
            return self.send_json({"ok": True})

        if path == "/api/restart":
            cur = _current_user(self)
            pid = body.get("id", "")
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if proj and not project_belongs_to(proj, cur):
                return self.send_json({"error": "Sin permiso"}, 403)
            if proj:
                t = threading.Thread(target=start_project, args=(proj,), daemon=True)
                t.start()
            return self.send_json({"ok": True})

        if path == "/api/webhook":
            # Receptor de webhooks de GitHub — redeploy automático al hacer push
            event     = self.headers.get("X-GitHub-Event", "")
            if event != "push":
                return self.send_json({"ok": True, "skipped": "not push"})
            repo_name = body.get("repository", {}).get("full_name", "")
            ref       = body.get("ref", "")
            branch    = ref.replace("refs/heads/", "")
            commit    = (body.get("commits") or [{}])[0]
            sha       = body.get("after", "")[:7]
            msg       = commit.get("message", "")[:80]
            pusher    = body.get("pusher", {}).get("name", "?")
            projs     = load_projects()
            matched   = [p for p in projs
                         if repo_name and repo_name.lower() in p.get("repo_url","").lower()
                         and p.get("branch","main") == branch
                         and p.get("auto_deploy", True)]
            for proj in matched:
                pid = proj["id"]
                clear_log_file(pid, "build")
                append_log(pid, f"[nexhost] Webhook recibido — push de {pusher} a {branch}")
                append_log(pid, f"[nexhost] Commit: {sha} — {msg}")
                stop_project(pid)
                t = threading.Thread(target=start_project, args=(proj,), daemon=True)
                t.start()
            return self.send_json({"ok": True, "redeployed": len(matched)})

        if path == "/api/delete":
            cur = _current_user(self)
            pid = body.get("id", "")
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if proj and not project_belongs_to(proj, cur):
                return self.send_json({"error": "Sin permiso"}, 403)
            stop_project(pid)
            stop_hf_watcher(pid)
            projs = [p for p in projs if p["id"] != pid]
            save_projects(projs)
            shutil.rmtree(BASE_DIR / pid, ignore_errors=True)       # código fuente
            shutil.rmtree(_get_env_dir(pid), ignore_errors=True)    # env Python (/tmp)
            shutil.rmtree(PROJECT_DATA_BASE / pid, ignore_errors=True)  # datos persistentes
            for lt in ("build", "process", ""):
                lf = _log_file(pid, lt)
                if lf.exists(): lf.unlink()
            hf = DATA_DIR / f"{pid}.history.json"
            if hf.exists(): hf.unlink()
            # Borrar también del HF Dataset si existe
            if proj and proj.get("persist_data"):
                threading.Thread(target=_purge_hf_project_folder,
                                 args=(proj,), daemon=True).start()
            return self.send_json({"ok": True})

        # ── Per-user GitHub token ───────────────────────────────────────────
        if path == "/api/user/gh-token":
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            new_tok = (body.get("token") or "").strip()
            update_user(cur["id"], gh_token=new_tok)
            return self.send_json({"ok": True,
                                   "preview": ("***" + new_tok[-4:]) if new_tok else ""})

        # ── Toggle de notificaciones por email ──────────────────────────────
        if path == "/api/user/notify":
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            update_user(cur["id"], notify_errors=bool(body.get("notify_errors", True)))
            return self.send_json({"ok": True})

        # ── Cambio de contraseña ────────────────────────────────────────────
        if path == "/api/user/password":
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            old = body.get("old_password", "")
            new = body.get("new_password", "")
            if cur.get("password_hash") and not _verify_password(old, cur["password_hash"]):
                return self.send_json({"error": "Contraseña actual incorrecta"}, 400)
            if not new or len(new) < 6:
                return self.send_json({"error": "Mínimo 6 caracteres"}, 400)
            update_user(cur["id"], password_hash=_hash_password(new))
            return self.send_json({"ok": True})

        # ── ADMIN: Banear (temp/perm) ───────────────────────────────────────
        if path == "/api/admin/ban":
            cur, ok = _require_admin(self)
            if not ok:
                return self.send_json({"error": "Sólo admin"}, 403)
            uid    = body.get("user_id", "")
            mode   = body.get("mode", "temp")    # "temp" | "perm" | "unban"
            hours  = int(body.get("hours", 24))
            reason = (body.get("reason") or "").strip()[:300]
            notify = bool(body.get("notify_email", True))
            target = get_user(uid)
            if not target:
                return self.send_json({"error": "Usuario no encontrado"}, 404)
            if target.get("role") == "admin":
                return self.send_json({"error": "No se puede banear a un admin"}, 400)

            now_ts = int(time.time())
            admin_username = cur.get("username", "admin")

            if mode == "unban":
                update_user(uid, banned_until=0, ban_reason="", ban_by="", ban_at=0)
            elif mode == "perm":
                update_user(uid, banned_until=-1, ban_reason=reason,
                            ban_by=admin_username, ban_at=now_ts)
                _revoke_all_user_tokens(uid)
            else:
                until = int(time.time() + max(1, hours) * 3600)
                update_user(uid, banned_until=until, ban_reason=reason,
                            ban_by=admin_username, ban_at=now_ts)
                _revoke_all_user_tokens(uid)

            # Notificación por email (si SendGrid disponible y modo != unban)
            email_sent = False
            if mode != "unban" and notify and SENDGRID_ENABLED:
                em = (target.get("email") or "").strip()
                if em and "@" in em:
                    if mode == "perm":
                        title = "Tu cuenta ha sido suspendida permanentemente"
                        dur_html = '<strong style="color:#ff6680">permanente</strong>'
                    else:
                        title = f"Tu cuenta ha sido suspendida temporalmente ({hours}h)"
                        dur_html = f'<strong style="color:#ffbe2e">{hours} horas</strong>'
                    safe_reason = (reason or "(sin motivo especificado)").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    html = f"""<!DOCTYPE html><html><body style="font-family:system-ui,sans-serif;background:#0a0a14;color:#e8e8f0;padding:24px;margin:0">
<div style="max-width:560px;margin:0 auto;background:#10101c;border:1px solid #2a2a44;border-radius:14px;overflow:hidden">
  <div style="padding:18px 22px;background:#1a0a14;border-bottom:1px solid #4a1a2a">
    <h2 style="margin:0;color:#ff6680;font-size:18px">⛔ NexHost — Cuenta suspendida</h2>
  </div>
  <div style="padding:22px">
    <p style="color:#c0c0d8;margin:0 0 14px">Hola <strong>{target.get('username','')}</strong>,</p>
    <p style="color:#c0c0d8;margin:0 0 14px">{title}.</p>
    <table style="width:100%;border-collapse:collapse;margin:14px 0;font-size:13px">
      <tr><td style="padding:6px 0;color:#80809a;width:120px">Duración</td><td>{dur_html}</td></tr>
      <tr><td style="padding:6px 0;color:#80809a">Aplicado por</td><td style="color:#e8e8f0;font-family:monospace">@{admin_username}</td></tr>
    </table>
    <div style="background:#050510;border-left:3px solid #ff6680;padding:12px 16px;margin:14px 0;border-radius:4px">
      <div style="color:#80809a;font-size:11px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">Motivo</div>
      <div style="color:#e8e8f0;font-size:13px;line-height:1.6">{safe_reason}</div>
    </div>
    <p style="color:#80809a;font-size:12px;margin:20px 0 0;line-height:1.6">Si crees que es un error, responde a este email para apelar.</p>
  </div>
  <div style="padding:14px 22px;background:#0a0a14;border-top:1px solid #2a2a44;color:#50506a;font-size:11px;font-family:monospace">
    NexHost · notificación de moderación
  </div>
</div>
</body></html>"""
                    threading.Thread(
                        target=send_email,
                        args=(em, f"[NexHost] {title}", html),
                        daemon=True,
                    ).start()
                    email_sent = True

            return self.send_json({"ok": True, "email_sent": email_sent})

        # ── ADMIN: Eliminar usuario completamente (incluye sus proyectos) ──
        if path == "/api/admin/delete-user":
            cur, ok = _require_admin(self)
            if not ok:
                return self.send_json({"error": "Sólo admin"}, 403)
            uid = body.get("user_id", "")
            if uid == cur.get("id"):
                return self.send_json({"error": "No puedes eliminarte a ti mismo"}, 400)
            target = get_user(uid)
            if not target:
                return self.send_json({"error": "Usuario no encontrado"}, 404)
            # 1. Borrar todos los proyectos del usuario
            projs = load_projects()
            user_projs = [p for p in projs if p.get("owner_id") == uid]
            for p in user_projs:
                pid = p["id"]
                try:
                    stop_project(pid)
                    stop_hf_watcher(pid)
                except Exception:
                    pass
                shutil.rmtree(BASE_DIR / pid, ignore_errors=True)
                shutil.rmtree(_get_env_dir(pid), ignore_errors=True)
                shutil.rmtree(PROJECT_DATA_BASE / pid, ignore_errors=True)
                for lt in ("build", "process", ""):
                    lf = _log_file(pid, lt)
                    if lf.exists():
                        try: lf.unlink()
                        except: pass
                hist = DATA_DIR / f"{pid}.history.json"
                if hist.exists():
                    try: hist.unlink()
                    except: pass
                if p.get("persist_data"):
                    threading.Thread(target=_purge_hf_project_folder,
                                     args=(p,), daemon=True).start()
            projs = [p for p in projs if p.get("owner_id") != uid]
            save_projects(projs)
            # 2. Borrar carpeta de datos personales
            shutil.rmtree(USER_DATA_BASE / uid, ignore_errors=True)
            # 3. Revocar sesiones e eliminar del registro
            _revoke_all_user_tokens(uid)
            users = load_users()
            users = [u for u in users if u.get("id") != uid]
            save_users(users)
            return self.send_json({"ok": True, "deleted_projects": len(user_projs)})

        # ── ADMIN: Broadcast email a todos los usuarios ─────────────────────
        if path == "/api/admin/broadcast":
            cur, ok = _require_admin(self)
            if not ok:
                return self.send_json({"error": "Sólo admin"}, 403)
            if not SENDGRID_ENABLED:
                return self.send_json({"error": "SendGrid no configurado (falta SENDGRID_API_KEY)"}, 400)
            subject  = (body.get("subject") or "").strip()
            message  = (body.get("message") or "").strip()
            audience = body.get("audience", "all")  # "all" | "active" | "admins"
            if not subject or not message:
                return self.send_json({"error": "Subject y mensaje son obligatorios"}, 400)
            if len(subject) > 200:
                return self.send_json({"error": "Subject demasiado largo (máx 200)"}, 400)
            if len(message) > 20000:
                return self.send_json({"error": "Mensaje demasiado largo (máx 20.000 caracteres)"}, 400)

            users = load_users()
            now = time.time()
            recipients = []
            for u in users:
                em = (u.get("email") or "").strip()
                if not em or "@" not in em:
                    continue
                # Excluir baneados permanentes
                bu = u.get("banned_until", 0)
                if bu == -1:
                    continue
                if audience == "active":
                    # Activos = login en los últimos 30 días
                    if (now - (u.get("last_login", 0) or 0)) > 30 * 86400:
                        continue
                elif audience == "admins":
                    if u.get("role") != "admin":
                        continue
                recipients.append(u)

            if not recipients:
                return self.send_json({"error": "No hay destinatarios que coincidan", "sent": 0}, 400)

            safe_msg_html = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            sender_name = cur.get("username", "admin")

            def _send_all(recip_list, subj, msg_html, msg_txt, sender):
                sent_ok = 0
                for u in recip_list:
                    try:
                        html = f"""<!DOCTYPE html><html><body style="font-family:system-ui,sans-serif;background:#0a0a14;color:#e8e8f0;padding:24px;margin:0">
<div style="max-width:560px;margin:0 auto;background:#10101c;border:1px solid #2a2a44;border-radius:14px;overflow:hidden">
  <div style="padding:18px 22px;background:#0a1a2a;border-bottom:1px solid #1a3a4a">
    <h2 style="margin:0;color:#00f5a0;font-size:18px">📢 NexHost — Mensaje del equipo</h2>
  </div>
  <div style="padding:22px">
    <p style="color:#c0c0d8;margin:0 0 14px">Hola <strong>{u.get('username','')}</strong>,</p>
    <div style="color:#e8e8f0;line-height:1.6;font-size:14px;margin:0 0 18px">{msg_html}</div>
    <p style="color:#80809a;font-size:11px;margin:24px 0 0;line-height:1.5">— {sender}, equipo NexHost</p>
  </div>
  <div style="padding:14px 22px;background:#0a0a14;border-top:1px solid #2a2a44;color:#50506a;font-size:11px;font-family:monospace">
    NexHost · comunicación oficial · puedes desactivar emails en tu perfil
  </div>
</div>
</body></html>"""
                        if send_email(u["email"], f"[NexHost] {subj}", html, msg_txt):
                            sent_ok += 1
                        time.sleep(0.05)  # throttle ligero para no saturar SendGrid
                    except Exception as e:
                        print(f"[nexhost] ⚠ broadcast a {u.get('email','?')} falló: {e}")
                print(f"[nexhost] 📢 Broadcast completado: {sent_ok}/{len(recip_list)} enviados")

            threading.Thread(
                target=_send_all,
                args=(recipients, subject, safe_msg_html, message, sender_name),
                daemon=True,
            ).start()
            return self.send_json({
                "ok": True,
                "queued": len(recipients),
                "audience": audience,
            })

        if path == "/api/env":
            pid = body.get("id", "")
            projs = load_projects()
            for p in projs:
                if p["id"] == pid:
                    p["env_vars"] = body.get("env_vars", {})
            save_projects(projs)
            return self.send_json({"ok": True})

        if path == "/api/config/gh-token":
            # Compat: ahora se guarda en el usuario actual (per-user)
            cur = _current_user(self)
            if not cur:
                return self.send_json({"error": "No autorizado"}, 401)
            new_tok = (body.get("token") or "").strip()
            update_user(cur["id"], gh_token=new_tok)
            return self.send_json({
                "ok": True,
                "preview": ("***" + new_tok[-4:]) if new_tok else "",
                "scope": "user",
            })

        if path == "/api/config-update":
            pid = body.get("id", "")
            projs = load_projects()
            for p in projs:
                if p["id"] == pid:
                    if "start_cmd"      in body: p["start_cmd"]      = body["start_cmd"]
                    if "deps_file"      in body: p["deps_file"]       = body["deps_file"]
                    if "python_version" in body:
                        old_ver = p.get("python_version", "")
                        new_ver = body["python_version"]
                        if old_ver != new_ver:
                            # Eliminar entorno viejo para forzar recreación
                            shutil.rmtree(_get_env_dir(pid), ignore_errors=True)
                        p["python_version"] = new_ver
                    if "branch"         in body: p["branch"]          = body["branch"]
                    if "auto_restart"   in body: p["auto_restart"]    = body["auto_restart"]
                    if "persist_data"   in body: p["persist_data"]    = bool(body["persist_data"])
                    if "hf_dataset_id"  in body: p["hf_dataset_id"]   = body["hf_dataset_id"]
                    if "no_web"         in body: p["no_web"]           = bool(body["no_web"])
            save_projects(projs)
            # Actualizar config en dataset si el proyecto usa HF Dataset
            updated_proj = next((p2 for p2 in projs if p2["id"] == pid), None)
            if updated_proj and updated_proj.get("persist_data") and _hf_dataset_id(updated_proj):
                threading.Thread(
                    target=hf_save_project_config,
                    args=(pid, updated_proj),
                    daemon=True
                ).start()
            return self.send_json({"ok": True})

        if path == "/api/detect":
            pid = body.get("id", "")
            work_dir = BASE_DIR / pid
            if work_dir.exists():
                cmd, deps = detect_project_files(work_dir)
                return self.send_json({"start_cmd": cmd, "deps_file": deps})
            return self.send_json({"start_cmd": "", "deps_file": ""})

        if path == "/api/clear-logs":
            pid      = body.get("id", "")
            log_type = body.get("log_type", "build")
            clear_log_file(pid, log_type)
            return self.send_json({"ok": True})

        if path == "/api/env-cleanup":
            # Fuerza la eliminación del entorno de un proyecto (para recrearlo)
            pid = body.get("id", "")
            env_dir = _get_env_dir(pid)
            shutil.rmtree(str(env_dir), ignore_errors=True)
            return self.send_json({"ok": True, "removed": str(env_dir)})

        if path == "/api/file-save":
            pid     = body.get("id", "")
            frel    = body.get("path", "")
            fcontent = body.get("content", "")
            work_dir = BASE_DIR / pid
            target  = (work_dir / frel).resolve()
            if not str(target).startswith(str(work_dir.resolve())):
                return self.send_json({"error": "Acceso denegado"}, 403)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fcontent, encoding="utf-8")
            # Si el proyecto tiene persist_data, subir el archivo al dataset HF
            projs = load_projects()
            proj  = next((p for p in projs if p["id"] == pid), None)
            if proj and proj.get("persist_data"):
                hf_upload_file(pid, proj, target, work_dir)
            return self.send_json({"ok": True})

        if path == "/api/file-delete":
            pid  = body.get("id", "")
            frel = body.get("path", "")
            work_dir = BASE_DIR / pid
            target = (work_dir / frel).resolve()
            if not str(target).startswith(str(work_dir.resolve())):
                return self.send_json({"error": "Acceso denegado"}, 403)
            if target.exists():
                target.unlink()
            return self.send_json({"ok": True})

        self.send_response(404); self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import io

    PORT = int(os.environ.get("PORT", 5000))

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    _persist_str = str(PERSIST)
    _persist_warn = " ⚠ TEMPORAL — activa Persistent Storage en HF" if "/tmp" in _persist_str else ""
    _meta_ds = _hf_meta_dataset()

    print("=" * 56)
    print("  NexHost v3 - Motor uv + mise")
    print("=" * 56)
    print(f"  Puerto   : {PORT}")
    print(f"  Storage  : {PERSIST}{_persist_warn}")
    print(f"  Backup   : {_meta_ds if _meta_ds else 'desactivado (añade BOT_DATA_TOKEN)'}")
    print(f"  SpaceID  : {os.environ.get('SPACE_ID', 'N/A')}")
    print(f"  Entornos : {ENVS_BASE}")
    print(f"  Motor    : {'uv (' + str(UV) + ')' if UV else 'pip (fallback)'}")
    print(f"  Runtimes : {'mise (' + str(_MISE) + ')' if _MISE else 'mise no disponible'}")
    print("=" * 56)
    sys.stdout.flush()

    # 2. Iniciar hilo de restauración
    threading.Thread(target=restore_on_boot, daemon=True).start()

    # 3. Configurar el servidor
    _HF_PORT = PORT
    server = HTTPServer(("0.0.0.0", _HF_PORT), Handler)

    print(f"  OK Servidor HTTP escuchando en :{_HF_PORT}")
    sys.stdout.flush()

    # Responder al ping de Hugging Face inmediatamente
    def _force_ready():
        try:
            time.sleep(1)
            requests.get(f"http://localhost:{_HF_PORT}/ping", timeout=5)
            print("  [DEBUG] Ping de salud enviado con éxito")
        except: pass
    threading.Thread(target=_force_ready, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Deteniendo NexHost...")
        server.server_close()