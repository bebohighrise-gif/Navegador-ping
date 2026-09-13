#!/usr/bin/env python3
"""Elimina todo el estado persistido asociado a una sesión Bebo."""
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def clean_dsn(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != "uselibpqcompat"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main():
    session = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    url = os.environ.get("DATABASE_URL")
    if not url or not session:
        return 0
    import psycopg2
    conn = psycopg2.connect(clean_dsn(url))
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS bebo_snapshots (id SERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), session_name TEXT NOT NULL DEFAULT 'bebo', payload TEXT NOT NULL)")
    cur.execute("ALTER TABLE bebo_snapshots ADD COLUMN IF NOT EXISTS session_name TEXT NOT NULL DEFAULT 'bebo'")
    cur.execute("CREATE TABLE IF NOT EXISTS bebo_deleted (path TEXT PRIMARY KEY, deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
    cur.execute("DELETE FROM bebo_snapshots WHERE session_name = %s", (session,))
    cur.execute("DELETE FROM bebo_deleted WHERE path LIKE %s", (session + "/%",))
    conn.commit()
    cur.close()
    conn.close()
    print(f"purged session data: {session}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
