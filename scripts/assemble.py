#!/usr/bin/env python3
"""Reconstruye server.js y public/* desde _payload/*.partXX (base64)."""
import base64
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "_payload"
if not PAYLOAD.is_dir():
    ROOT = Path("/app")
    PAYLOAD = ROOT / "_payload"

if not PAYLOAD.is_dir():
    print("no _payload — skip assemble")
    raise SystemExit(0)

groups = defaultdict(list)
for p in sorted(PAYLOAD.glob("*.part*")):
    idx = p.name.rfind(".part")
    stem = p.name[:idx]
    groups[stem].append(p)

for stem, parts in groups.items():
    parts = sorted(parts, key=lambda x: x.name)
    b64 = "".join(x.read_text().strip() for x in parts)
    data = base64.b64decode(b64)
    out = ROOT / stem.replace("__", "/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("assembled", out, len(data))
