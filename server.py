import asyncio
import json
import os
import pty
import websockets

PORT = int(os.environ.get("PORT", 8080))

async def handle_connection(websocket):
    # Crear una pseudoterminal (PTY) interactiva en Linux
    master_fd, slave_fd = pty.openpty()
    
    # Iniciar la shell Bash en la PTY dentro de /workspace
    proc = await asyncio.create_subprocess_exec(
        '/bin/bash',
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd='/workspace',
        preexec_fn=os.setsid
    )
    os.close(slave_fd)

    loop = asyncio.get_running_loop()

    # Tarea 1: Leer la salida de Bash y transmitirla por WebSocket en tiempo real
    async def stream_output():
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 1024)
                if not data:
                    break
                await websocket.send(json.dumps({
                    "type": "output",
                    "data": data.decode('utf-8', errors='ignore')
                }))
            except Exception:
                break

    output_task = asyncio.create_task(stream_output())

    # Tarea 2: Recibir comandos por WebSocket y escribirlos en la PTY
    try:
        async for message in websocket:
            payload = json.loads(message)
            if payload.get("type") == "input":
                cmd = payload.get("data", "")
                os.write(master_fd, cmd.encode('utf-8'))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        output_task.cancel()
        os.close(master_fd)
        if proc.returncode is None:
            proc.terminate()

async def main():
    # Healthcheck HTTP básico en la misma puerto para Render
    async def http_handler(path, request_headers):
        if path == "/":
            return (200, [("Content-Type", "text/plain")], b"OK\n")
        return None

    async with websockets.serve(
        handle_connection,
        "0.0.0.0",
        PORT,
        process_request=http_handler
    ):
        await asyncio.Future()  # Mantener servidor corriendo

if __name__ == "__main__":
    asyncio.run(main())
