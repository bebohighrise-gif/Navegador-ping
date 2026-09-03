import asyncio
import json
import os

import websockets


async def must_reject(uri, **kwargs):
    async with websockets.connect(uri, **kwargs) as websocket:
        await asyncio.wait_for(websocket.wait_closed(), timeout=3)
        assert websocket.close_code == 1008, websocket.close_code


async def main():
    base = "ws://127.0.0.1:8765"
    token = os.environ["BEBO_SHELL_TOKEN"]
    await must_reject(f"{base}/?project=test-project")
    await must_reject(
        f"{base}/?project=../escape",
        additional_headers={"Authorization": f"Bearer {token}"},
    )
    # Los límites de tamaño/timeout son opcionales (0 = ilimitado).
    # Este test solo valida auth y path traversal.
    async with websockets.connect(
        f"{base}/?project=test-project",
        additional_headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        await websocket.send(json.dumps({"type": "input", "data": "printf SECURE_OK\\n"}))
        for _ in range(10):
            msg = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            if msg.get("type") == "output" and "SECURE_OK" in msg.get("data", ""):
                print("SECURITY_OK")
                return
        raise AssertionError("No se recibió salida tras auth válida")


if __name__ == "__main__":
    asyncio.run(main())
