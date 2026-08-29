import os
import asyncio
import json
import pty
import fcntl
import termios
import struct
import websockets

PORT = int(os.environ.get("WS_PORT", 8765))

async def handle_client(websocket, path):
    # Crear la pseudoterminal (PTY) bash
    pid, master_fd = pty.fork()
    
    if pid == 0:
        # Proceso hijo: Iniciar bash interactivo en /workspace
        os.chdir("/workspace")
        os.execvp("bash", ["bash"])
    else:
        # Proceso padre: Manejar la entrada/salida de la terminal mediante WebSocket
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        loop = asyncio.get_event_loop()

        def read_pty():
            try:
                data = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                if data:
                    asyncio.create_task(websocket.send(json.dumps({"type": "output", "data": data})))
            except (OSError, Exception):
                pass

        loop.add_reader(master_fd, read_pty)

        try:
            async for message in websocket:
                payload = json.loads(message)
                if payload.get("type") == "input":
                    command = payload.get("data", "")
                    os.write(master_fd, command.encode('utf-8'))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            loop.remove_reader(master_fd)
            os.close(master_fd)

async def main():
    async with websockets.serve(handle_client, "0.0.0.0", PORT):
        print(f"[SERVER PTY] Servidor WebSocket ejecutándose en el puerto {PORT}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
