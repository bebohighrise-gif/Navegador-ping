import asyncio
import json
import websockets


async def main():
    async with websockets.connect("ws://127.0.0.1:8765") as websocket:
        await websocket.send(json.dumps({"type": "input", "data": "printf READY\\n"}))
        for _ in range(10):
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
            if message.get("type") == "output" and "READY" in message.get("data", ""):
                print("WEBSOCKET_OK")
                return
        raise AssertionError("No se recibió la salida esperada del PTY")


if __name__ == "__main__":
    asyncio.run(main())
