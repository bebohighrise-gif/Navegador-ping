# Navegador-ping / Bebo AI Host

Kernel de hosting ligero para proyectos web con:

- **Router HTTP/WebSocket** (`router.js`) – proxy inverso por subdominio basado en PostgreSQL
- **Servidor PTY sandboxed** (`server.py`) – shell interactivo por WebSocket con autenticación por token y aislamiento con bubblewrap
- **Host Manager** (`host_manager.py`) – despliega proyectos, asigna puertos libres y lanza procesos en tmux
- **Browser bot** (`browser_bot.js`) – captura de pantallas y extracción de texto con Puppeteer/Chromium

## Requisitos

- Docker (recomendado) o:
  - Node.js ≥ 18
  - Python ≥ 3.10
  - PostgreSQL
  - Chromium / Chrome
  - tmux + bubblewrap (opcional pero recomendado)

## Variables de entorno importantes

| Variable | Descripción | Default |
|----------|-------------|---------| 
| `PORT` | Puerto del router HTTP | `8080` |
| `WS_PTY_PORT` | Puerto del servidor PTY | `8765` |
| `DATABASE_URL` | Cadena de conexión a Postgres | – |
| `BEBO_SHELL_TOKEN` | Token Bearer para el PTY (obligatorio) | – |
| `WORKSPACE_ROOT` | Raíz de los proyectos | `/workspace/proyectos` |
| `ALLOW_DEFAULT_WS_HOSTS` | Hosts que pueden usar el PTY por defecto (coma-separados) | – |
| `PUPPETEER_EXECUTABLE_PATH` | Ruta a Chromium | `/usr/bin/chromium` |
| `MAX_COMMAND_BYTES` | Límite de bytes por comando PTY | `2048` |
| `MAX_OUTPUT_BYTES` | Límite de salida por sesión | `512000` |
| `COMMAND_TIMEOUT_SECONDS` | Timeout de comando | `90` |

## Arranque con Docker

```bash
docker build -t navegador-ping .
docker run --rm -p 8080:8080 -p 8765:8765 \
  -e DATABASE_URL="postgres://..." \
  -e BEBO_SHELL_TOKEN="tu-token-secreto" \
  -e ALLOW_DEFAULT_WS_HOSTS="tu-dominio.com" \
  navegador-ping
```

## Uso del Host Manager

```bash
# Desplegar un proyecto
python3 host_manager.py deploy mi-app mi-app.ejemplo.com "npm start"

# Listar
python3 host_manager.py list

# Detener
python3 host_manager.py stop mi-app
```

## Tests

```bash
export BEBO_SHELL_TOKEN=...
python3 test_ws.py
python3 test_security.py
# (con el router levantado en el puerto de prueba)
ROUTER_TEST_PORT=18088 python3 test_proxy_ws.py
```

## Mejoras incluidas en esta versión

- Arranque correcto de ambos procesos con manejo de señales (entrypoint)
- Esquema de BD unificado entre `router.js` y `host_manager.py` (incluye `updated_at` + trigger)
- Validación estricta de nombres de proyecto y subdominio
- Comparación de token en tiempo constante aproximado
- Healthcheck HTTP (`/healthz`)
- SSL de Postgres configurable y más seguro por defecto
- Chromium correcto en Ubuntu 24.04
- Browser bot con mejor manejo de errores y timeouts
- Página de “host no registrado” más limpia
- `.gitignore` y `package.json` actualizados

## Seguridad

- El PTY **exige** `BEBO_SHELL_TOKEN`.
- Los nombres de proyecto solo permiten caracteres alfanuméricos + `-_`.
- Se usa bubblewrap cuando está disponible.
- Límites de tamaño de comando y de salida.
- Timeout de comandos.
- Proxy solo a `127.0.0.1` en los puertos registrados.
