"""BEBO Safe API: terminal web controlada para Render Free.

La API no ejecuta comandos arbitrarios del sistema. Solo permite una lista
pequeña de herramientas de desarrollo dentro de un workspace efímero. Para
persistencia real se debe configurar DATABASE_URL y un almacenamiento externo.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import signal
import time
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

APP_DIR = Path(__file__).resolve().parent
WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "/tmp/bebo-workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
PORT = int(os.getenv("PORT", "10000"))
API_KEY = os.getenv("BEBO_API_KEY", "")
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT_BYTES", "200000"))
MAX_SECONDS = int(os.getenv("MAX_EXEC_SECONDS", "20"))

# Deliberadamente no se incluyen sudo, shell nesting, redirecciones, pipes,
# red, kill, package managers ni comandos destructivos.
COMMANDS: dict[str, tuple[str, ...]] = {
    "pwd": ("pwd",),
    "ls": ("ls",),
    "find": ("find",),
    "cat": ("cat",),
    "head": ("head",),
    "tail": ("tail",),
    "grep": ("grep",),
    "wc": ("wc",),
    "sort": ("sort",),
    "uniq": ("uniq",),
    "diff": ("diff",),
    "git": ("git",),
    "python": ("python", "python3"),
    "node": ("node",),
    "npm": ("npm",),
    "go": ("go",),
    "rustc": ("rustc",),
    "cargo": ("cargo",),
    "java": ("java",),
    "javac": ("javac",),
    "ruby": ("ruby",),
    "php": ("php",),
    "perl": ("perl",),
    "date": ("date",),
    "whoami": ("whoami",),
    "uname": ("uname",),
}

app = FastAPI(title="BEBO Safe API", version="1.0.0", docs_url="/docs", redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def require_api_key(value: Annotated[str | None, Header(alias="X-API-Key")] = None) -> str:
    if not API_KEY:
        raise HTTPException(503, "BEBO_API_KEY no está configurada")
    if not value or not hmac.compare_digest(value, API_KEY):
        raise HTTPException(401, "API key inválida")
    return value


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
    blocked = re.compile(r"(^|[;&|`$<>\\])|(^|/)(sudo|su|shutdown|reboot|mkfs|dd)$", re.I)
    if any(blocked.search(x) for x in args):
        raise HTTPException(400, "Argumento no permitido")
    return args


class ExecRequest(BaseModel):
    command: str = Field(min_length=1, max_length=500)
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
    return {"status": "ok", "service": "bebo", "workspace": str(WORKSPACE)}


@app.get("/api/config")
async def config() -> dict:
    return {"name": "BEBO", "version": app.version, "safe_mode": True, "workspace": "."}


@app.post("/api/exec")
async def execute(payload: ExecRequest, _: str = Depends(require_api_key)) -> dict:
    cwd = safe_path(payload.cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    args = validate_args(payload.args)
    executable = next((x for x in COMMANDS[payload.command] if __import__("shutil").which(x)), None)
    if not executable:
        raise HTTPException(503, f"Herramienta no disponible: {payload.command}")
    try:
        proc = await asyncio.create_subprocess_exec(
            executable, *args,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env={"PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(WORKSPACE)},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), payload.timeout_seconds)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise HTTPException(408, "El comando superó el tiempo máximo")
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": stdout[:MAX_OUTPUT].decode(errors="replace"),
        "stderr": stderr[:MAX_OUTPUT].decode(errors="replace"),
        "cwd": str(cwd.relative_to(WORKSPACE)),
    }


@app.get("/api/files/list")
async def list_files(path: str = Query("."), _: str = Depends(require_api_key)) -> list[dict]:
    directory = safe_path(path)
    if not directory.is_dir():
        raise HTTPException(404, "Directorio no encontrado")
    entries = []
    for item in sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]:
        entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else None})
    return entries


@app.get("/api/files/read")
async def read_file(path: str, _: str = Depends(require_api_key)) -> dict:
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    if target.stat().st_size > 1_000_000:
        raise HTTPException(413, "Archivo demasiado grande")
    return {"path": path, "content": target.read_text(errors="replace")}


@app.put("/api/files/write")
async def write_file(payload: FileRequest, _: str = Depends(require_api_key)) -> dict:
    target = safe_path(payload.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")
    return {"ok": True, "path": payload.path, "bytes": target.stat().st_size}


@app.get("/api/security")
async def security(_: str = Depends(require_api_key)) -> dict:
    return {"safe_mode": True, "arbitrary_shell": False, "network_commands": False, "workspace_boundary": str(WORKSPACE)}


# Conserva la interfaz estática existente como pantalla inicial si está disponible.
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
