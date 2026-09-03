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
    async with websockets.connect(
        f"{base}/?project=test-project",
        additional_headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        await websocket.send(json.dumps({"type": "input", "data": "x" * 3000}))
        error = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
        assert error["error"] == "command_too_large", error
        await websocket.send(json.dumps({"type": "input", "data": "sleep 5\n"}))
        while True:
            error = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            if error.get("type") == "error":
                assert error["error"] == "command_timeout", error
                break
    print("SECURITY_OK")


if __name__ == "__main__":
    asyncio.run(main())
