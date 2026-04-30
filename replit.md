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

## User system (multi-user)

NexHost is now multi-user. Users are stored in `~/.nexhost/data/users.json`.

- **Admin** (`username: admin`) is auto-created from `NEXHOST_PASSWORD` (default: `nexhost`). The admin sees and manages every user/project, and can use the global `GH_TOKEN` env secret as a fallback.
- **Regular users** register with `username + email + password` or sign in with **Google OAuth**. Each user has an isolated project list (filtered by `owner_id` = Google `sub` or generated id) and an optional **per-user GitHub token** stored in their record.
- **Per-user storage**: persistent user data lives under `~/.nexhost/usuarios/<user_id>/`.
- **Banning**: admin can temporarily ban (X hours), permanently ban or fully delete a user (also wipes all their projects and HF dataset folders).
- **Email notifications**: when a user's project crashes (returncode ≠ 0 / SIGTERM / SIGKILL), the dueño receives an HTML email via SendGrid (`SENDGRID_API_KEY`). Toggleable per user via `notify_errors`.

## API surface (multi-user)

Public:
- `POST /api/login` — `{identifier, password}` (identifier = username or email). Returns server-side error messages (banned, locked, captcha required, wrong creds) — the UI displays them verbatim.
- `POST /api/register` — `{username, email, password}`.
- `GET  /api/google/start` — 302 to Google's consent screen.
- `GET  /api/google/callback` — links existing email or creates a new account.

Authenticated:
- `GET  /api/config` — now also returns `user`, `is_admin`, `google_oauth`, `sendgrid`, `gh_token_from_user`.
- `GET  /api/me` — full current user record (sans password).
- `POST /api/user/gh-token` — `{token}` per-user GitHub token.
- `POST /api/user/notify` — toggle email notifications.
- `POST /api/user/password` — change own password.
- Project endpoints (`/api/deploy`, `/api/delete`, `/api/stop`, `/api/restart`, `/api/projects`) verify the caller is the project's `owner_id` (admin bypasses).

Admin only:
- `GET  /api/admin/users` — list all users with stats.
- `GET  /api/admin/stats` — global system stats (users / projects / services).
- `POST /api/admin/ban` — `{user_id, mode: 'temp'|'perm'|'unban', hours?}`.
- `POST /api/admin/delete-user` — wipes user + all their projects + HF folders.
- `POST /api/admin/broadcast` — `{subject, message, audience: 'all'|'active'|'admins'}`. Sends a SendGrid email to every user matching the audience (excludes perma-banned and users without email). Background-threaded with light throttling.

## Default credentials

- Login password defaults to `nexhost` (override with `NEXHOST_PASSWORD` env var).
- Google OAuth: set `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (already configured).
- SendGrid: set `SENDGRID_API_KEY` (already configured). Default `From:` is `noreply@nexhost.app` (override with `SENDGRID_FROM`).
- Cloudflare Turnstile is optional (set `CF_TURNSTILE_SITE_KEY` and `CF_TURNSTILE_SECRET` to enable).

## Recent Changes

- 2026-04-30: **Admin broadcast + UX polish.** Added `/api/admin/broadcast` (SendGrid) and `/api/admin/stats` endpoints. Admin panel rebuilt with three tabs: Usuarios, Email masivo (audience selector + subject + message + live recipient count), Estado (system health: users/projects/services with status dots). Added persistent banner above all views prompting users without their own GitHub token to connect — dismissible per session, auto-hides when token is saved.
- 2026-04-30: **Multi-user refactor.** Added full user system (`users.json`, PBKDF2-hashed passwords), email/username login, registration UI with tabs, Google OAuth (`/api/google/*`), per-user GitHub tokens, project ownership filtering, admin panel with ban/unban/delete, SendGrid email notifications on project crash. Fixed login.html to display real server error messages (was hardcoded "Contraseña incorrecta"). Fixed `_canonical_base()` to honour `NEXHOST_DOMAIN` / `REPLIT_DEV_DOMAIN` and `X-Forwarded-Proto` (resolves the URL hash 404 bug). Project deletion now also purges the HF dataset folder when `persist_data` is on.
- 2026-04-30: Imported from GitHub. Switched the hardcoded `7860` port to use `PORT` env var (default `5000`) so it works in the Replit preview. Added `pyproject.toml` with the runtime Python deps. Configured the workflow and `vm` deployment target.
