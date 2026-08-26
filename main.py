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
import uuid
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
DB_URL = os.getenv("DATABASE_URL", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_D1_DATABASE_ID = os.getenv("CLOUDFLARE_D1_DATABASE_ID", "642e3286-81b5-4821-90b3-7713b0e504f0").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
DB_POOL = None
API_RECORDS: dict[str, dict] = {}
COMMAND_HISTORY: list[dict] = []
PROJECTS: dict[str, dict] = {}
SCHEDULED_TASKS: dict[str, dict] = {}
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
    "pytest": ("pytest",), "ruff": ("ruff",), "black": ("black",), "mypy": ("mypy",),
    "pip": ("pip", "pip3"), "pipx": ("pipx",), "uv": ("uv",),
    "pnpm": ("pnpm",), "yarn": ("yarn",), "vite": ("vite",), "deno": ("deno",), "bun": ("bun",),
    "gcc": ("gcc",), "g++": ("g++",), "clang": ("clang",), "make": ("make",),
    "cmake": ("cmake",), "mvn": ("mvn",), "gradle": ("gradle",), "dotnet": ("dotnet",),
    "swift": ("swift",), "kotlin": ("kotlin",),
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


async def valid_api_key(value: str | None) -> bool:
    if not value:
        return False
    if hmac.compare_digest(value, API_KEY):
        return True
    digest = key_digest(value)
    return any(item.get("is_active") and hmac.compare_digest(item.get("key_hash", ""), digest) for item in await load_api_records())


async def require_api_key(value: Annotated[str | None, Header(alias="X-API-Key")] = None) -> str:
    if not await valid_api_key(value):
        raise HTTPException(401, "API key inválida")
    return value or ""


async def require_private_access(request: Request, session: Annotated[str | None, Cookie(alias="bebo_session")] = None, value: Annotated[str | None, Header(alias="X-API-Key")] = None) -> str:
    if session_ok(session):
        return "session"
    if await valid_api_key(value):
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


class ApiCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def key_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def d1_query(sql: str, params: list | None = None) -> list[dict]:
    if not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_D1_DATABASE_ID and CLOUDFLARE_API_TOKEN):
        return []
    import httpx
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/d1/database/{CLOUDFLARE_D1_DATABASE_ID}/query"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"}, json={"sql": sql, "params": params or []})
        response.raise_for_status()
        body = response.json()
        if not body.get("success"):
            raise RuntimeError(str(body.get("errors")))
        result = body.get("result") or []
        return (result[0].get("results") or []) if result else []


async def load_api_records() -> list[dict]:
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        return await d1_query("SELECT id, name, key_hash, is_active, created_at, updated_at FROM api_keys ORDER BY created_at DESC")
    if DB_POOL:
        rows = await DB_POOL.fetch("SELECT id, name, key_hash, is_active, created_at, updated_at FROM bebo_api_keys ORDER BY created_at DESC")
        return [dict(row) for row in rows]
    return list(API_RECORDS.values())


async def save_api_record(record: dict) -> None:
    API_RECORDS[record["id"]] = record
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("INSERT INTO api_keys(id,name,key_hash,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,key_hash=excluded.key_hash,is_active=excluded.is_active,updated_at=excluded.updated_at", [record["id"], record["name"], record["key_hash"], 1 if record["is_active"] else 0, record["created_at"], record["updated_at"]])
        return
    if DB_POOL:
        await DB_POOL.execute("""INSERT INTO bebo_api_keys(id,name,key_hash,is_active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(id) DO UPDATE SET name=$2,key_hash=$3,is_active=$4,updated_at=$6""", record["id"], record["name"], record["key_hash"], record["is_active"], record["created_at"], record["updated_at"])


@app.on_event("startup")
async def initialize_storage() -> None:
    global DB_POOL
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        try:
            await d1_query("CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            for record in await load_api_records():
                API_RECORDS[record["id"]] = record
        except Exception as exc:
            print(f"[bebo] D1 no disponible: {exc}")
        return
    if not DB_URL:
        return
    try:
        import asyncpg
        DB_POOL = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
        await DB_POOL.execute("""CREATE TABLE IF NOT EXISTS bebo_api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""")
        for record in await load_api_records():
            API_RECORDS[record["id"]] = record
    except Exception as exc:
        DB_POOL = None
        print(f"[bebo] PostgreSQL no disponible; APIs nuevas serán temporales: {exc}")


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


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    command_line: str = Field(min_length=1, max_length=300)
    cwd: str = Field(default=".", max_length=300)
    port_internal: int | None = Field(default=None, ge=1024, le=65535)


class TaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    command_line: str = Field(min_length=1, max_length=300)
    cron_expr: str = Field(min_length=5, max_length=80)
    cwd: str = Field(default=".", max_length=300)


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


@app.get("/api/apis")
async def list_apis(_: str = Depends(require_session)) -> list[dict]:
    return [{"id": item["id"], "name": item["name"], "is_active": item["is_active"], "created_at": str(item["created_at"]), "updated_at": str(item["updated_at"])} for item in await load_api_records()]


@app.post("/api/apis")
async def create_api(payload: ApiCreateRequest, _: str = Depends(require_session)) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    raw_key = "bebo_" + secrets.token_urlsafe(32)
    record = {"id": str(uuid.uuid4()), "name": payload.name.strip(), "key_hash": key_digest(raw_key), "is_active": True, "created_at": now, "updated_at": now}
    await save_api_record(record)
    return {"id": record["id"], "name": record["name"], "api_key": raw_key, "warning": "Guarda esta clave; por seguridad no volverá a mostrarse completa."}


@app.post("/api/apis/{api_id}/regenerate")
async def regenerate_api(api_id: str, _: str = Depends(require_session)) -> dict:
    records = await load_api_records()
    record = next((x for x in records if x["id"] == api_id), None)
    if not record:
        raise HTTPException(404, "API no encontrada")
    raw_key = "bebo_" + secrets.token_urlsafe(32)
    record["key_hash"] = key_digest(raw_key); record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await save_api_record(record)
    return {"id": api_id, "name": record["name"], "api_key": raw_key, "warning": "La clave anterior fue revocada."}


@app.delete("/api/apis/{api_id}")
async def delete_api(api_id: str, _: str = Depends(require_session)) -> dict:
    if api_id not in API_RECORDS and not DB_POOL and not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN):
        raise HTTPException(404, "API no encontrada")
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("DELETE FROM api_keys WHERE id=?", [api_id])
    elif DB_POOL:
        result = await DB_POOL.execute("DELETE FROM bebo_api_keys WHERE id=$1", api_id)
        if result.endswith("0"):
            raise HTTPException(404, "API no encontrada")
    API_RECORDS.pop(api_id, None)
    return {"ok": True, "id": api_id}


@app.get("/api/projects")
async def list_projects(_: str = Depends(require_private_access)) -> list[dict]:
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        return await d1_query("SELECT id,name,command_line,cwd,status,port_internal,created_at,updated_at FROM projects ORDER BY created_at DESC")
    return list(PROJECTS.values())


@app.post("/api/projects")
async def create_project(payload: ProjectRequest, _: str = Depends(require_session)) -> dict:
    safe_path(payload.cwd)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); item = {"id": str(uuid.uuid4()), "name": payload.name.strip(), "command_line": payload.command_line.strip(), "cwd": payload.cwd, "status": "stopped", "port_internal": payload.port_internal, "created_at": now, "updated_at": now}
    PROJECTS[item["id"]] = item
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("INSERT INTO projects(id,name,command_line,cwd,status,port_internal,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", [item["id"], item["name"], item["command_line"], item["cwd"], item["status"], item["port_internal"], now, now])
    return item


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, _: str = Depends(require_session)) -> dict:
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("DELETE FROM projects WHERE id=?", [project_id])
    elif project_id not in PROJECTS:
        raise HTTPException(404, "Proyecto no encontrado")
    PROJECTS.pop(project_id, None); return {"ok": True, "id": project_id}


@app.get("/api/tasks")
async def list_tasks(_: str = Depends(require_private_access)) -> list[dict]:
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        return await d1_query("SELECT id,name,command_line,cron_expr,cwd,is_active,last_run,next_run FROM scheduled_tasks ORDER BY name")
    return list(SCHEDULED_TASKS.values())


@app.post("/api/tasks")
async def create_task(payload: TaskRequest, _: str = Depends(require_session)) -> dict:
    safe_path(payload.cwd); now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); item = {"id": str(uuid.uuid4()), "name": payload.name.strip(), "command_line": payload.command_line.strip(), "cron_expr": payload.cron_expr.strip(), "cwd": payload.cwd, "is_active": True, "last_run": None, "next_run": None, "created_at": now}
    SCHEDULED_TASKS[item["id"]] = item
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("INSERT INTO scheduled_tasks(id,name,command_line,cron_expr,cwd,is_active) VALUES(?,?,?,?,?,1)", [item["id"], item["name"], item["command_line"], item["cron_expr"], item["cwd"]])
    return item


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, _: str = Depends(require_session)) -> dict:
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("DELETE FROM scheduled_tasks WHERE id=?", [task_id])
    elif task_id not in SCHEDULED_TASKS:
        raise HTTPException(404, "Tarea no encontrada")
    SCHEDULED_TASKS.pop(task_id, None); return {"ok": True, "id": task_id}


@app.get("/api/capabilities")
async def capabilities(_: str = Depends(require_private_access)) -> dict:
    available = {name: any(shutil.which(exe) for exe in variants) for name, variants in COMMANDS.items()}
    return {"safe_mode": True, "available_commands": [name for name, ok in available.items() if ok], "unavailable_commands": [name for name, ok in available.items() if not ok], "persistent_storage": bool(DB_POOL or (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)), "workspace_persistent": False, "background_processes": False, "network_shell": False}


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
    result = {"ok": proc.returncode == 0, "exit_code": proc.returncode, "stdout": stdout[:MAX_OUTPUT].decode(errors="replace"), "stderr": stderr[:MAX_OUTPUT].decode(errors="replace"), "cwd": str(cwd.relative_to(WORKSPACE))}
    event = {"id": str(uuid.uuid4()), "command": payload.command, "cwd": result["cwd"], "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "exit_code": proc.returncode, "output": result["stdout"][:2000]}
    COMMAND_HISTORY.insert(0, event); del COMMAND_HISTORY[100:]
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("INSERT INTO command_history(id,user_key_hash,command,cwd,timestamp,exit_code,output_ref) VALUES(?,?,?,?,?,?,?)", [event["id"], "owner", payload.command, event["cwd"], event["timestamp"], event["exit_code"], event["output"]])
    return result


@app.get("/api/history")
async def history(_: str = Depends(require_private_access)) -> list[dict]:
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        return await d1_query("SELECT id, command, cwd, timestamp, exit_code, output_ref AS output FROM command_history ORDER BY timestamp DESC LIMIT 100")
    return COMMAND_HISTORY


@app.delete("/api/history")
async def clear_history(_: str = Depends(require_session)) -> dict:
    COMMAND_HISTORY.clear()
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        await d1_query("DELETE FROM command_history")
    return {"ok": True}


@app.get("/api/files/list")
async def list_files(path: str = Query("."), _: str = Depends(require_private_access)) -> list[dict]:
    directory = safe_path(path)
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        prefix = "" if path in ("", ".") else path.strip("./") + "/"
        rows = await d1_query("SELECT path, size, updated_at FROM workspace_files WHERE path LIKE ? ORDER BY path LIMIT 500", [prefix + "%"])
        seen: dict[str, dict] = {}
        for row in rows:
            rest = row["path"][len(prefix):]
            name = rest.split("/", 1)[0]
            seen[name] = {"name": name, "type": "directory" if "/" in rest else "file", "size": None if "/" in rest else row["size"], "updated_at": row["updated_at"]}
        return sorted(seen.values(), key=lambda x: (x["type"] != "directory", x["name"].lower()))
    if not directory.is_dir():
        raise HTTPException(404, "Directorio no encontrado")
    return [{"name": item.name, "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else None} for item in sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]]


@app.get("/api/files/read")
async def read_file(path: str, _: str = Depends(require_private_access)) -> dict:
    target = safe_path(path)
    relative = str(target.relative_to(WORKSPACE))
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        rows = await d1_query("SELECT path, content, size, updated_at FROM workspace_files WHERE path=?", [relative])
        if not rows:
            raise HTTPException(404, "Archivo no encontrado")
        return {"path": relative, "content": rows[0]["content"], "size": rows[0]["size"], "updated_at": rows[0]["updated_at"]}
    if not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    if target.stat().st_size > 1_000_000:
        raise HTTPException(413, "Archivo demasiado grande")
    return {"path": path, "content": target.read_text(errors="replace")}


@app.put("/api/files/write")
async def write_file(payload: FileRequest, _: str = Depends(require_private_access)) -> dict:
    target = safe_path(payload.path)
    relative = str(target.relative_to(WORKSPACE))
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        if len(payload.content.encode("utf-8")) > 900_000:
            raise HTTPException(413, "Archivo demasiado grande para almacenamiento D1")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await d1_query("INSERT INTO workspace_files(path,content,size,updated_at) VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET content=excluded.content,size=excluded.size,updated_at=excluded.updated_at", [relative, payload.content, len(payload.content.encode("utf-8")), now])
        return {"ok": True, "path": relative, "bytes": len(payload.content.encode("utf-8")), "persistent": True}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")
    return {"ok": True, "path": payload.path, "bytes": target.stat().st_size, "persistent": False}


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
