# Navegador-ping · Bebo AI Host

Kernel de hosting profesional: proxy inverso por subdominio, PTY sandboxed y despliegue de proyectos.

## En Render: **solo 1 secreto obligatorio**

| Variable | ¿Obligatoria? | Notas |
|----------|---------------|--------|
| `DATABASE_URL` | **Sí** | La genera el add-on Postgres de Render |
| `APP_SECRET` | Opcional | Si la pones, el token de shell se deriva de ella (más limpio) |
| `BEBO_SHELL_TOKEN` | Opcional | Override explícito del token |

**No necesitas configurar `BEBO_SHELL_TOKEN`.**  
Si solo existe `DATABASE_URL`, el sistema deriva automáticamente un token estable con HMAC.  
Si defines `APP_SECRET`, se usa esa como fuente (recomendado a medio plazo).

Render rellena solo:
- `RENDER_EXTERNAL_HOSTNAME` → se añade automáticamente a los hosts permitidos del PTY.

### Deploy en Render

1. Crea un **Web Service** desde este repo.
2. Añade el add-on **PostgreSQL** (inyecta `DATABASE_URL`).
3. (Opcional) Añade `APP_SECRET` con un valor largo aleatorio.
4. Dockerfile ya está listo. El puerto es el que Render inyecta (`PORT`).

```bash
# Comprobar salud
curl https://tu-servicio.onrender.com/healthz
```

## Componentes

| Archivo | Rol |
|---------|-----|
| `router.js` | Proxy HTTP/WS + rutas desde Postgres |
| `server.py` | PTY WebSocket sandboxed (bubblewrap) |
| `host_manager.py` | `deploy` / `list` / `stop` de proyectos |
| `browser_bot.js` | Capturas con Puppeteer |
| `secret_utils.py` | Derivación de token (menos secretos) |
| `docker-entrypoint.sh` | Arranque limpio de ambos procesos |

## Variables de entorno (completas)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PORT` | `8080` | Router HTTP |
| `WS_PTY_PORT` | `8765` | Servidor PTY |
| `DATABASE_URL` | – | Postgres |
| `APP_SECRET` | – | Secreto maestro (opcional) |
| `BEBO_SHELL_TOKEN` | derivado | Token PTY |
| `WORKSPACE_ROOT` | `/workspace/proyectos` | Raíz de proyectos |
| `ALLOW_DEFAULT_WS_HOSTS` | auto | Hosts extra para PTY |
| `MAX_COMMAND_BYTES` | `2048` | Límite comando |
| `MAX_OUTPUT_BYTES` | `512000` | Límite salida |
| `COMMAND_TIMEOUT_SECONDS` | `90` | Timeout |
| `LOG_LEVEL` | `info` | `info` / `silent` |

## Uso local / Docker

```bash
docker build -t navegador-ping .
docker run --rm -p 8080:8080 -p 8765:8765 \
  -e DATABASE_URL="postgres://..." \
  -e APP_SECRET="cambia-esto-por-uno-largo" \
  navegador-ping
```

### Host Manager

```bash
python3 host_manager.py deploy mi-app mi-app.ejemplo.com "npm start"
python3 host_manager.py list
python3 host_manager.py stop mi-app
```

### Tests

```bash
export BEBO_SHELL_TOKEN=...   # o deja que se derive
python3 test_ws.py
python3 test_security.py
```

## Seguridad

- Token de shell obligatorio (explícito o derivado).
- Nombres de proyecto solo `[a-zA-Z0-9_-]`.
- bubblewrap cuando está disponible.
- Límites de tamaño y timeout de comandos.
- Proxy solo a `127.0.0.1` en puertos registrados.
- Healthcheck en `/healthz`.

## Licencia

Privado / UNLICENSED.
