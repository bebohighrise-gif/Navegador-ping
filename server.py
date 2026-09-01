import asyncio
import json
import os
import pty
import fcntl
import termios
import struct
import logging
import asyncio
import websockets

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("WS_PTY_PORT", 8765))

def set_pty_size(master_fd, cols, rows):
    """Ajusta el tamaño de la terminal PTY (soporte para resize)."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

async def pty_handler(websocket):
    master, slave = pty.openpty()
    logger.info("Nueva conexión, PTY creado")

    proc = await asyncio.create_subprocess_exec(
        '/bin/bash',
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=os.setsid
    )
    os.close(slave)
    logger.info(f"Proceso bash iniciado con PID {proc.pid}")

    loop = asyncio.get_running_loop()
    reader_registered = True

    def websocket_is_open():
        state = getattr(websocket, "state", None)
        if state is not None:
            return getattr(state, "name", "") == "OPEN"
        return not getattr(websocket, "closed", True)

    def pty_read_callback():
        nonlocal reader_registered
        try:
            data = os.read(master, 4096)
            if not data:
                logger.info("PTY EOF, cerrando conexión")
                loop.remove_reader(master)
                reader_registered = False
                if websocket_is_open():
                    asyncio.create_task(websocket.close())
                return

            # Solo enviar si el socket sigue abierto
            if websocket_is_open():
                msg = json.dumps({"type": "output", "data": data.decode('utf-8', errors='ignore')})
                asyncio.create_task(websocket.send(msg))
        except Exception as e:
            logger.error(f"Error en pty_read_callback: {e}")
            if reader_registered:
                loop.remove_reader(master)
                reader_registered = False
            if websocket_is_open():
                asyncio.create_task(websocket.close())

    loop.add_reader(master, pty_read_callback)

    try:
        async for message in websocket:
            try:
                payload = json.loads(message)
                msg_type = payload.get("type")

                if msg_type == "input" and "data" in payload:
                    os.write(master, payload["data"].encode('utf-8'))
                elif msg_type == "resize":
                    cols = payload.get("cols", 80)
                    rows = payload.get("rows", 24)
                    set_pty_size(master, cols, rows)
                    logger.debug(f"Resize PTY a {cols}x{rows}")
                else:
                    if isinstance(message, str):
                        os.write(master, message.encode('utf-8'))
                    else:
                        logger.warning(f"Mensaje no reconocido: {message}")
            except json.JSONDecodeError:
                os.write(master, message.encode('utf-8'))
            except Exception as e:
                logger.error(f"Error procesando mensaje: {e}")
    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"Conexión cerrada: {e}")
    finally:
        if reader_registered:
            loop.remove_reader(master)
            reader_registered = False
        try:
            os.close(master)
        except OSError:
            pass

        if proc.returncode is None:
            logger.info(f"Terminando proceso {proc.pid}")
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                logger.warning(f"Proceso {proc.pid} no respondió, forzando kill")
                proc.kill()
                await proc.wait()
        logger.info("Recursos liberados")

async def main():
    logger.info(f"Servidor PTY escuchando en 0.0.0.0:{PORT}")
    async with websockets.serve(pty_handler, "0.0.0.0", PORT):
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servidor detenido por el usuario")
