# BEBO Safe Shell

BEBO es una API FastAPI con consola web para ejecutar un conjunto controlado de herramientas de desarrollo dentro de un workspace. Esta versión está diseñada para desplegarse en **Render Free** sin convertir el servicio en una puerta de ejecución arbitraria sobre el host.

## Seguridad

El servicio exige `X-API-Key` y compara la clave con una comparación constante. Las rutas se resuelven dentro de `WORKSPACE_DIR` y se rechazan intentos de escape mediante `..`, enlaces peligrosos o rutas absolutas. La ejecución utiliza `create_subprocess_exec`, no `bash -c`, y solo acepta comandos incluidos en una lista segura. Se bloquean shell nesting, redirecciones, pipes, sustitución de comandos, `sudo`, operaciones destructivas, gestión de procesos y herramientas de red.

El contenedor se ejecuta como el usuario sin privilegios `bebo`. Cada comando tiene un tiempo máximo, salida limitada y un entorno mínimo. El objetivo es ofrecer una consola de desarrollo útil, no una VM Linux ni acceso administrativo al sistema.

## Acceso privado y credenciales

La web está protegida por una contraseña de propietario configurada en `BEBO_ADMIN_PASSWORD`. Tras iniciar sesión en `/`, BEBO crea automáticamente una API key en memoria y la entrega en la sesión privada. Para conservar la misma API key después de reinicios o nuevos despliegues, copia esa clave en Render como `BEBO_API_KEY`; si se deja vacía, se generará una clave nueva al iniciar el contenedor. La contraseña nunca se muestra ni se guarda en el repositorio.

La sesión se mantiene en una cookie HttpOnly, SameSite Strict y con duración limitada. Los endpoints privados aceptan la sesión web o la API key en el header `X-API-Key`.

## Desarrollo local

```bash
export BEBO_API_KEY='cambia-esta-clave'
python3 -m pip install fastapi 'uvicorn[standard]' pydantic
uvicorn main:app --reload --port 10000
```

La interfaz está disponible en `http://localhost:10000` y la documentación OpenAPI en `/docs`. La comprobación de estado pública es `/health`.

## Despliegue en Render

El repositorio incluye `Dockerfile` y `render.yaml`. En Render, crea un Web Service desde este repositorio, selecciona el plan Free y define una variable secreta `BEBO_API_KEY` con una clave larga y aleatoria. Render asigna automáticamente `PORT`.

El workspace predeterminado es `/tmp/bebo-workspace`, por lo que los archivos locales pueden perderse tras reinicios o nuevos despliegues. La nueva D1 de Cloudflare `navegador-ping-bebo-db` tiene el ID `642e3286-81b5-4821-90b3-7713b0e504f0`; su esquema está versionado en `d1-schema.sql`. BEBO puede consultar D1 mediante la API HTTP de Cloudflare cuando configures `CLOUDFLARE_ACCOUNT_ID` y `CLOUDFLARE_API_TOKEN`, pero **los archivos del workspace todavía no están sincronizados con almacenamiento de objetos externo**. No debe utilizarse SQLite ni guardar secretos en el repositorio. El token de Cloudflare debe ser un secreto de Render con permisos mínimos para D1. Para no perder archivos, la siguiente integración debe conectar S3/R2/Supabase Storage mediante variables secretas y guardar allí cada archivo antes de confirmar la operación.

## Panel privado de APIs

Después de iniciar sesión, el panel **Mis APIs** permite crear una API con nombre, generar automáticamente una clave independiente, regenerarla —revocando la anterior— y eliminarla. Las claves se almacenan como hashes SHA-256 y nunca se muestran en el listado. La clave completa aparece únicamente al crearla o regenerarla.

Los endpoints son `GET /api/apis`, `POST /api/apis`, `POST /api/apis/{id}/regenerate` y `DELETE /api/apis/{id}`. Si `DATABASE_URL` está configurada, los nombres, hashes, estados y fechas se guardan en PostgreSQL externo. Si configuras D1, BEBO usa la nueva base de Cloudflare como prioridad. Sin PostgreSQL ni D1 configurados, funcionan solo temporalmente en memoria.

## API principal

La lista segura incluye utilidades de archivos y diagnóstico (`pwd`, `ls`, `find`, `cat`, `head`, `tail`, `grep`, `wc`, `sort`, `uniq`, `diff`, `date`, `whoami`, `uname`), Git, Python, Node/npm, Go, Rust/Cargo, Java, Ruby, PHP y Perl. También se aceptan herramientas de calidad y compilación si están presentes en la imagen: pytest, ruff, black, mypy, pip, pipx, uv, pnpm, yarn, vite, deno, bun, gcc, g++, clang, make, cmake, Maven, Gradle, .NET, Swift y Kotlin. El endpoint `GET /health` comprueba el servicio sin autenticación. `POST /api/exec` ejecuta un comando seguro usando `command`, `args`, `cwd` y `timeout_seconds`. `GET /api/files/list` lista el workspace. `GET /api/files/read` lee archivos pequeños y `PUT /api/files/write` guarda archivos dentro del workspace. `GET /api/security` devuelve el estado de las barreras activas. `GET /api/capabilities` informa qué herramientas están instaladas en el contenedor y qué capacidades persistentes están activas.

## Cobertura del prompt original

Esta versión implementa el núcleo seguro: login de propietario, generación y gestión de APIs, autenticación por API key, ejecución limitada de comandos, validación de rutas, lectura/escritura de archivos, una lista ampliada de herramientas de desarrollo, detección de capacidades, interfaz web, Dockerfile y despliegue en Render. **No implementa todavía todas las capacidades del prompt original**. Quedan pendientes el almacenamiento externo de archivos, proyectos persistentes en PostgreSQL, tmux, procesos en background, cron persistente, streaming SSE, proxy/ngrok, SSH/SCP/RSYNC, instalación de lenguajes, administración de procesos, usuarios múltiples, Git avanzado, compilación, depuración, cifrado, backups y una shell Bash/Zsh arbitraria. Estas funciones no deben activarse sin un sandbox aislado y límites de seguridad adicionales.

## Limitaciones de Render Free

Render Free puede suspender Web Services sin tráfico, reiniciarlos y eliminar los cambios del sistema de archivos local. El servicio gratuito no ofrece SSH, disco persistente ni garantía de ejecución continua. Por eso BEBO no debe utilizarse en este plan para workers 24/7, bots permanentes, servidores de juego o tareas críticas. El almacenamiento y el estado deben vivir fuera del contenedor.

## Principios de producción

Antes de aceptar usuarios externos, cambia la clave de ejemplo, restringe `CORS_ORIGINS` a los dominios reales, añade PostgreSQL, incorpora un sistema de usuarios con hash de credenciales y coloca límites por usuario y dirección IP. Para permitir una shell Linux completa, usa una VM o sandbox aislado; no amplíes esta lista segura directamente en un Web Service público.
