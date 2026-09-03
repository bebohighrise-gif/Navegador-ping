"""Utilidades de secretos para Navegador-ping / Bebo AI Host."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional


def derive_shell_token(app_secret: str, purpose: str = "bebo-shell-v1") -> str:
    """Deriva un token estable a partir de APP_SECRET (HMAC-SHA256, hex truncado)."""
    digest = hmac.new(
        app_secret.encode("utf-8"),
        purpose.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:48]


def resolve_shell_token() -> str:
    """
    Orden de prioridad:
    1. BEBO_SHELL_TOKEN (explícito)
    2. Derivado de APP_SECRET
    3. Derivado de DATABASE_URL (fallback estable en Render)
    4. Token aleatorio (solo desarrollo; se imprime un aviso)
    """
    explicit = (os.environ.get("BEBO_SHELL_TOKEN") or "").strip()
    if explicit:
        return explicit

    app_secret = (os.environ.get("APP_SECRET") or "").strip()
    if app_secret:
        return derive_shell_token(app_secret)

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if db_url:
        # Fallback estable: el usuario solo necesita DATABASE_URL en Render
        return derive_shell_token(db_url, purpose="bebo-shell-from-db-v1")

    # Último recurso (dev)
    token = secrets.token_urlsafe(32)
    print(
        "[SECURITY] No hay APP_SECRET ni BEBO_SHELL_TOKEN ni DATABASE_URL. "
        f"Usando token temporal de esta sesión: {token}",
        flush=True,
    )
    return token


def is_render() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
