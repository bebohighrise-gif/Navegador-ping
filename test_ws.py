import asyncio
import json
import os

import websockets


async def main():
    token = os.environ["BEBO_SHELL_TOKEN"]
    uri = "ws://127.0.0.1:8765/?project=test-project"
    headers = {"Authorization": f"Bearer {token}"}
    async with websockets.connect(uri, additional_headers=headers) as websocket:
        await websocket.send(json.dumps({"type": "input", "data": "printf READY\\n"}))
        for _ in range(10):
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
            if message.get("type") == "output" and "READY" in message.get("data", ""):
                print("WEBSOCKET_OK")
                return
        raise AssertionError("No se recibió la salida esperada del PTY")


if __name__ == "__main__":
    asyncio.run(main())
