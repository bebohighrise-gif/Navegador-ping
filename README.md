# BEBO Safe Shell

BEBO es una API FastAPI con consola web para ejecutar un conjunto controlado de herramientas de desarrollo dentro de un workspace. Esta versión está diseñada para desplegarse en **Render Free** sin convertir el servicio en una puerta de ejecución arbitraria sobre el host.

## Seguridad

El servicio exige `X-API-Key` y compara la clave con una comparación constante. Las rutas se resuelven dentro de `WORKSPACE_DIR` y se rechazan intentos de escape mediante `..`, enlaces peligrosos o rutas absolutas. La ejecución utiliza `create_subprocess_exec`, no `bash -c`, y solo acepta comandos incluidos en una lista segura. Se bloquean shell nesting, redirecciones, pipes, sustitución de comandos, `sudo`, operaciones destructivas, gestión de procesos y herramientas de red.

El contenedor se ejecuta como el usuario sin privilegios `bebo`. Cada comando tiene un tiempo máximo, salida limitada y un entorno mínimo. El objetivo es ofrecer una consola de desarrollo útil, no una VM Linux ni acceso administrativo al sistema.

## Desarrollo local

```bash
export BEBO_API_KEY='cambia-esta-clave'
python3 -m pip install fastapi 'uvicorn[standard]' pydantic
uvicorn main:app --reload --port 10000
```

La interfaz está disponible en `http://localhost:10000` y la documentación OpenAPI en `/docs`. La comprobación de estado pública es `/health`.

## Despliegue en Render

El repositorio incluye `Dockerfile` y `render.yaml`. En Render, crea un Web Service desde este repositorio, selecciona el plan Free y define una variable secreta `BEBO_API_KEY` con una clave larga y aleatoria. Render asigna automáticamente `PORT`.

El workspace predeterminado es `/tmp/bebo-workspace`, por lo que los archivos locales pueden perderse tras reinicios o nuevos despliegues. Para datos persistentes, la próxima capa debe conectar PostgreSQL mediante `DATABASE_URL` y un almacenamiento de objetos externo. No debe utilizarse SQLite ni guardar secretos en el repositorio.

## API principal

`GET /health` comprueba el servicio sin autenticación. `POST /api/exec` ejecuta un comando seguro usando `command`, `args`, `cwd` y `timeout_seconds`. `GET /api/files/list` lista el workspace. `GET /api/files/read` lee archivos pequeños y `PUT /api/files/write` guarda archivos dentro del workspace. `GET /api/security` devuelve el estado de las barreras activas.

## Limitaciones de Render Free

Render Free puede suspender Web Services sin tráfico, reiniciarlos y eliminar los cambios del sistema de archivos local. El servicio gratuito no ofrece SSH, disco persistente ni garantía de ejecución continua. Por eso BEBO no debe utilizarse en este plan para workers 24/7, bots permanentes, servidores de juego o tareas críticas. El almacenamiento y el estado deben vivir fuera del contenedor.

## Principios de producción

Antes de aceptar usuarios externos, cambia la clave de ejemplo, restringe `CORS_ORIGINS` a los dominios reales, añade PostgreSQL, incorpora un sistema de usuarios con hash de credenciales y coloca límites por usuario y dirección IP. Para permitir una shell Linux completa, usa una VM o sandbox aislado; no amplíes esta lista segura directamente en un Web Service público.
