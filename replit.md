# NexHost

NexHost is a Python-based hosting engine originally designed for Hugging Face Spaces. It manages and runs user projects with isolated environments using `uv` and `mise`.

## Project Structure

- `main.py` — Single-file HTTP server (Python `http.server`) that powers NexHost. Handles authentication, project management, file uploads, and process supervision.
- `login.html` — Login page served on unauthenticated requests.
- `static/` — Static assets (greeting page, index).
- `pyproject.toml` — Python project dependencies (managed by `uv`).
- `Dockerfile` — Original Hugging Face Spaces container definition (kept for reference).

## Tech Stack

- **Language**: Python 3.12
- **HTTP server**: stdlib `http.server.HTTPServer`
- **Dependencies**: `huggingface_hub`, `requests`
- **Package manager**: `uv`

## Replit Setup

- **Workflow**: `Start application` runs `python main.py` and binds to `0.0.0.0:5000`.
- **Port**: The server listens on `PORT` (default `5000`). On Hugging Face it would default to `7860`.
- **Deployment**: Configured as `vm` (always-running) since this is a stateful hosting engine that manages background processes.

## Default credentials

- Login password defaults to `nexhost` (override with `NEXHOST_PASSWORD` env var).
- Cloudflare Turnstile is optional (set `CF_TURNSTILE_SITE_KEY` and `CF_TURNSTILE_SECRET` to enable).

## Recent Changes

- 2026-04-30: Imported from GitHub. Switched the hardcoded `7860` port to use `PORT` env var (default `5000`) so it works in the Replit preview. Added `pyproject.toml` with the runtime Python deps. Configured the workflow and `vm` deployment target.
