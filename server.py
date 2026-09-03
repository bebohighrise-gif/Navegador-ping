#!/usr/bin/env python3
"""
Servidor WebSocket + PTY sandboxed para Bebo AI / Navegador-ping.
Autenticación por token, aislamiento por proyecto con bubblewrap y límites de recurso.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import termios
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import websockets

from secret_utils import is_render, resolve_shell_token

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pty-server")

PORT = int(os.environ.get("WS_PTY_PORT", "8765"))
SHELL_TOKEN = resolve_shell_token()
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace/proyectos")).resolve()
# 0 = sin límite práctico. Por defecto sin tope de tamaño de proyecto/sesión.
MAX_COMMAND_BYTES = int(os.environ.get("MAX_COMMAND_BYTES", "0"))  # 0 = ilimitado
MAX_OUTPUT_BYTES = int(os.environ.get("MAX_OUTPUT_BYTES", "0"))  # 0 = ilimitado
COMMAND_TIMEOUT_SECONDS = float(os.environ.get("COMMAND_TIMEOUT_SECONDS", "0"))  # 0 = sin timeout


def set_pty_size(master_fd: int, cols: int, rows: int) -> None:
    cols = max(1, min(int(cols), 500))
    rows = max(1, min(int(rows), 500))
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)


def extract_request_path(websocket) -> str:
    request = getattr(websocket, "request", None)
    if request is not None:
        return getattr(request, "path", "") or ""
    return getattr(websocket, "path", "") or ""


def extract_headers(websocket):
    request = getattr(websocket, "request", None)
    headers = getattr(request, "headers", None) if request is not None else None
    return headers or getattr(websocket, "request_headers", {}) or {}


def constant_time_equal(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def authenticate(websocket) -> bool:
    if not SHELL_TOKEN:
        logger.error("No hay token de shell configurado; rechazando conexión")
        return False

    headers = extract_headers(websocket)
    authorization = headers.get("Authorization") or headers.get("authorization") or ""
    supplied = authorization.removeprefix("Bearer ").strip()

    if not supplied:
        query = parse_qs(urlsplit(extract_request_path(websocket)).query)
        supplied = (query.get("token") or [""])[0]

    return constant_time_equal(supplied, SHELL_TOKEN)


def requested_project(websocket):
    query = parse_qs(urlsplit(extract_request_path(websocket)).query)
    slug = (query.get("project") or [""])[0]

    if not slug or Path(slug).name != slug or slug in {".", ".."}:
        return None
    if not all(c.isalnum() or c in "-_" for c in slug):
        return None

    project_root = (WORKSPACE_ROOT / slug).resolve()
    try:
        project_root.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return None

    project_root.mkdir(parents=True, exist_ok=True)
    return project_root


def sandbox_command(project_root: Path) -> list:
    bwrap = "/usr/bin/bwrap"
    if os.path.isfile(bwrap) and os.access(bwrap, os.X_OK):
        return [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--bind", str(project_root), str(project_root),
            "--chdir", str(project_root),
            "/bin/bash", "--noprofile", "--norc", "-i",
        ]
    logger.warning("bubblewrap no disponible; aislamiento solo por cwd")
    return ["/bin/bash", "--noprofile", "--norc", "-i"]


async def terminate_process(proc) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def pty_handler(websocket):
    if not authenticate(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    project_root = requested_project(websocket)
    if project_root is None:
        await websocket.close(code=1008, reason="project is required or invalid")
        return

    master, slave = pty.openpty()
    proc = None
    output_bytes = 0
    output_truncated = False
    timeout_task = None
    loop = asyncio.get_running_loop()
    reader_registered = False

    def websocket_is_open() -> bool:
        state = getattr(websocket, "state", None)
        if state is not None:
            return getattr(state, "name", "") == "OPEN"
        return not getattr(websocket, "closed", True)

    async def command_timeout():
        if COMMAND_TIMEOUT_SECONDS <= 0:
            return
        await asyncio.sleep(COMMAND_TIMEOUT_SECONDS)
        if proc and proc.returncode is None:
            logger.warning("Timeout de comando para proyecto %s", project_root.name)
            await terminate_process(proc)
            if websocket_is_open():
                try:
                    await websocket.send(json.dumps({"type": "error", "error": "command_timeout"}))
                except Exception:
                    pass

    def pty_read_callback():
        nonlocal output_bytes, output_truncated, reader_registered
        try:
            data = os.read(master, 4096)
            if not data:
                if reader_registered:
                    loop.remove_reader(master)
                    reader_registered = False
                return

            if MAX_OUTPUT_BYTES > 0 and output_bytes >= MAX_OUTPUT_BYTES:
                if not output_truncated and websocket_is_open():
                    output_truncated = True
                    asyncio.create_task(
                        websocket.send(
                            json.dumps({"type": "output", "data": "\n[output truncated]\n"})
                        )
                    )
                return

            if MAX_OUTPUT_BYTES > 0:
                remaining = MAX_OUTPUT_BYTES - output_bytes
                chunk = data[:remaining]
            else:
                chunk = data
            output_bytes += len(chunk)

            if websocket_is_open():
                asyncio.create_task(
                    websocket.send(
                        json.dumps(
                            {
                                "type": "output",
                                "data": chunk.decode("utf-8", errors="replace"),
                            }
                        )
                    )
                )

            if len(chunk) < len(data):
                output_truncated = True
        except (OSError, RuntimeError) as error:
            logger.error("Error leyendo PTY: %s", error)
            if reader_registered:
                try:
                    loop.remove_reader(master)
                except Exception:
                    pass
                reader_registered = False

    try:
        proc = await asyncio.create_subprocess_exec(
            *sandbox_command(project_root),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(project_root),
            start_new_session=True,
            env={
                **os.environ,
                "PWD": str(project_root),
                "HOME": str(project_root),
                "TERM": "xterm-256color",
            },
        )
        os.close(slave)
        slave = None

        loop.add_reader(master, pty_read_callback)
        reader_registered = True
        logger.info("PTY iniciado para proyecto %s (PID %s)", project_root.name, proc.pid)

        async for message in websocket:
            if isinstance(message, bytes):
                if MAX_COMMAND_BYTES > 0 and len(message) > MAX_COMMAND_BYTES:
                    await websocket.send(json.dumps({"type": "error", "error": "command_too_large"}))
                    continue
                try:
                    command_data = message.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    await websocket.send(json.dumps({"type": "error", "error": "invalid_utf8"}))
                    continue
            else:
                if MAX_COMMAND_BYTES > 0 and len(message.encode("utf-8")) > MAX_COMMAND_BYTES:
                    await websocket.send(json.dumps({"type": "error", "error": "command_too_large"}))
                    continue
                command_data = message

            try:
                payload = json.loads(command_data)
            except json.JSONDecodeError:
                payload = {"type": "input", "data": command_data}

            msg_type = payload.get("type")

            if msg_type == "input" and isinstance(payload.get("data"), str):
                data = payload["data"]
                if MAX_COMMAND_BYTES > 0 and len(data.encode("utf-8")) > MAX_COMMAND_BYTES:
                    await websocket.send(json.dumps({"type": "error", "error": "command_too_large"}))
                    continue

                if data.endswith("\n"):
                    if timeout_task and not timeout_task.done():
                        timeout_task.cancel()
                    if COMMAND_TIMEOUT_SECONDS > 0:
                        timeout_task = asyncio.create_task(command_timeout())

                os.write(master, data.encode("utf-8"))

            elif msg_type == "resize":
                set_pty_size(master, payload.get("cols", 80), payload.get("rows", 24))
            else:
                await websocket.send(json.dumps({"type": "error", "error": "invalid_message"}))

    except websockets.exceptions.ConnectionClosed:
        logger.info("Conexión cerrada para proyecto %s", project_root.name)
    except Exception as exc:
        logger.exception("Error inesperado en PTY de %s: %s", project_root.name, exc)
    finally:
        if timeout_task and not timeout_task.done():
            timeout_task.cancel()
        if reader_registered:
            try:
                loop.remove_reader(master)
            except Exception:
                pass
        if proc:
            await terminate_process(proc)
        try:
            os.close(master)
        except OSError:
            pass
        if slave is not None:
            try:
                os.close(slave)
            except OSError:
                pass


async def main():
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    env_label = "Render" if is_render() else "local"
    logger.info(
        "PTY escuchando en 0.0.0.0:%s | workspace=%s | env=%s | token_source=%s",
        PORT,
        WORKSPACE_ROOT,
        env_label,
        "env/derived",
    )

    async with websockets.serve(
        pty_handler,
        "0.0.0.0",
        PORT,
        max_size=(
            max(MAX_COMMAND_BYTES, MAX_OUTPUT_BYTES)
            if (MAX_COMMAND_BYTES > 0 and MAX_OUTPUT_BYTES > 0)
            else 50 * 1024 * 1024  # 50 MiB por frame WS si no hay límite configurado
        ),
        ping_interval=30,
        ping_timeout=20,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servidor detenido por el usuario")
