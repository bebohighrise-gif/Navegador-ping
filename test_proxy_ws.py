import asyncio
import json
import os

import websockets


async def main():
    token = os.environ["BEBO_SHELL_TOKEN"]
    port = os.environ.get("ROUTER_TEST_PORT", "18088")
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/?project=proxy-project",
        additional_headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        await websocket.send(json.dumps({"type": "input", "data": "printf PROXY_OK\\n"}))
        for _ in range(10):
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
            if message.get("type") == "output" and "PROXY_OK" in message.get("data", ""):
                print("PROXY_WEBSOCKET_OK")
                return
        raise AssertionError("No se recibió salida a través del router")


if __name__ == "__main__":
    asyncio.run(main())
