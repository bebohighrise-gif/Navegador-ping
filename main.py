"""BEBO Safe API: consola web privada y ejecución controlada para Render Free."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import shutil
import signal
import time
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

APP_DIR = Path(__file__).resolve().parent
WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "/tmp/bebo-workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT_BYTES", "200000"))
MAX_SECONDS = int(os.getenv("MAX_EXEC_SECONDS", "20"))
ADMIN_PASSWORD = os.getenv("BEBO_ADMIN_PASSWORD", "")
# Se genera automáticamente si no se define en Render. Para conservarla entre
# reinicios, copia la primera clave mostrada en Render como BEBO_API_KEY.
API_KEY = os.getenv("BEBO_API_KEY") or secrets.token_urlsafe(32)
SESSIONS: dict[str, float] = {}
SESSION_TTL = 8 * 60 * 60

COMMANDS: dict[str, tuple[str, ...]] = {
    "pwd": ("pwd",), "ls": ("ls",), "find": ("find",), "cat": ("cat",),
    "head": ("head",), "tail": ("tail",), "grep": ("grep",), "wc": ("wc",),
    "sort": ("sort",), "uniq": ("uniq",), "diff": ("diff",), "git": ("git",),
    "python": ("python", "python3"), "node": ("node",), "npm": ("npm",),
    "go": ("go",), "rustc": ("rustc",), "cargo": ("cargo",), "java": ("java",),
    "javac": ("javac",), "ruby": ("ruby",), "php": ("php",), "perl": ("perl",),
    "date": ("date",), "whoami": ("whoami",), "uname": ("uname",),
}

app = FastAPI(title="BEBO Safe API", version="1.1.0", docs_url="/docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",")], allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-API-Key"])


def clean_sessions() -> None:
    now = time.time()
    for token, expiry in list(SESSIONS.items()):
        if expiry < now:
            SESSIONS.pop(token, None)


def issue_session() -> str:
    clean_sessions()
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = time.time() + SESSION_TTL
    return token


def session_ok(token: str | None) -> bool:
    clean_sessions()
    return bool(token and token in SESSIONS and SESSIONS[token] > time.time())


def require_session(session: Annotated[str | None, Cookie(alias="bebo_session")] = None) -> str:
    if not session_ok(session):
        raise HTTPException(401, "Sesión no iniciada")
    return session or ""


def require_api_key(value: Annotated[str | None, Header(alias="X-API-Key")] = None) -> str:
    if not value or not hmac.compare_digest(value, API_KEY):
        raise HTTPException(401, "API key inválida")
    return value


def require_private_access(request: Request, session: Annotated[str | None, Cookie(alias="bebo_session")] = None, value: Annotated[str | None, Header(alias="X-API-Key")] = None) -> str:
    if session_ok(session):
        return "session"
    if value and hmac.compare_digest(value, API_KEY):
        return "api-key"
    raise HTTPException(401, "Autenticación requerida")


def safe_path(raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise HTTPException(400, "Ruta inválida")
    candidate = (WORKSPACE / raw).resolve()
    try:
        candidate.relative_to(WORKSPACE)
    except ValueError as exc:
        raise HTTPException(400, "La ruta debe permanecer dentro del workspace") from exc
    return candidate


def validate_args(args: list[str]) -> list[str]:
    if len(args) > 12 or any(len(x) > 300 for x in args):
        raise HTTPException(400, "Demasiados argumentos o argumento demasiado largo")
    blocked = re.compile(r"(^|[;&|`$<>\\])|(^|/)(sudo|su|shutdown|reboot|mkfs|dd|kill|pkill)$", re.I)
    if any(blocked.search(x) for x in args):
        raise HTTPException(400, "Argumento no permitido")
    return args


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ExecRequest(BaseModel):
    command: str = Field(min_length=1, max_length=30)
    cwd: str = Field(default=".", max_length=300)
    args: list[str] = Field(default_factory=list, max_length=12)
    timeout_seconds: int = Field(default=10, ge=1, le=MAX_SECONDS)

    @field_validator("command")
    @classmethod
    def command_name(cls, value: str) -> str:
        if value not in COMMANDS:
            raise ValueError("comando no incluido en la lista segura")
        return value


class FileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(default="", max_length=1_000_000)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "bebo", "private": True}


@app.post("/api/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Configura BEBO_ADMIN_PASSWORD en Render")
    if not hmac.compare_digest(payload.password, ADMIN_PASSWORD):
        raise HTTPException(401, "Contraseña incorrecta")
    token = issue_session()
    response.set_cookie("bebo_session", token, httponly=True, secure=os.getenv("RENDER", "") == "true", samesite="strict", max_age=SESSION_TTL, path="/")
    return {"ok": True, "message": "Sesión iniciada"}


@app.post("/api/logout")
async def logout(response: Response, session: Annotated[str | None, Cookie(alias="bebo_session")] = None) -> dict:
    if session:
        SESSIONS.pop(session, None)
    response.delete_cookie("bebo_session", path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(_: str = Depends(require_session)) -> dict:
    return {"authenticated": True, "user": "owner"}


@app.get("/api/credentials")
async def credentials(_: str = Depends(require_session)) -> dict:
    return {"api_key": API_KEY, "generated": not bool(os.getenv("BEBO_API_KEY")), "warning": "Guárdala como BEBO_API_KEY en Render para conservarla tras reinicios." if not os.getenv("BEBO_API_KEY") else ""}


@app.get("/api/config")
async def config(_: str = Depends(require_private_access)) -> dict:
    return {"name": "BEBO", "version": app.version, "safe_mode": True, "workspace": ".", "commands": sorted(COMMANDS)}


@app.post("/api/exec")
async def execute(payload: ExecRequest, _: str = Depends(require_private_access)) -> dict:
    cwd = safe_path(payload.cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    args = validate_args(payload.args)
    executable = next((x for x in COMMANDS[payload.command] if shutil.which(x)), None)
    if not executable:
        raise HTTPException(503, f"Herramienta no disponible: {payload.command}")
    try:
        proc = await asyncio.create_subprocess_exec(executable, *args, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, env={"PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(WORKSPACE)})
        stdout, stderr = await asyncio.wait_for(proc.communicate(), payload.timeout_seconds)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise HTTPException(408, "El comando superó el tiempo máximo")
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode, "stdout": stdout[:MAX_OUTPUT].decode(errors="replace"), "stderr": stderr[:MAX_OUTPUT].decode(errors="replace"), "cwd": str(cwd.relative_to(WORKSPACE))}


@app.get("/api/files/list")
async def list_files(path: str = Query("."), _: str = Depends(require_private_access)) -> list[dict]:
    directory = safe_path(path)
    if not directory.is_dir():
        raise HTTPException(404, "Directorio no encontrado")
    return [{"name": item.name, "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else None} for item in sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]]


@app.get("/api/files/read")
async def read_file(path: str, _: str = Depends(require_private_access)) -> dict:
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    if target.stat().st_size > 1_000_000:
        raise HTTPException(413, "Archivo demasiado grande")
    return {"path": path, "content": target.read_text(errors="replace")}


@app.put("/api/files/write")
async def write_file(payload: FileRequest, _: str = Depends(require_private_access)) -> dict:
    target = safe_path(payload.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")
    return {"ok": True, "path": payload.path, "bytes": target.stat().st_size}


@app.get("/api/security")
async def security(_: str = Depends(require_private_access)) -> dict:
    return {"safe_mode": True, "arbitrary_shell": False, "network_commands": False, "workspace_boundary": str(WORKSPACE), "single_owner": True}


STATIC_DIR = APP_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        index_file = STATIC_DIR / "bebo-safe.html"
        return FileResponse(index_file) if index_file.exists() else JSONResponse({"service": "BEBO"})
else:
    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {"service": "BEBO", "docs": "/docs"}
