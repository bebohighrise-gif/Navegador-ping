# Bebo AI · Consola Linux

Consola web hacker con **tmux real**, multi-sesión, explorador, proxy de puertos (sin pagar dominio), sonidos y CRT.

## Persistencia (todo nace de la base de datos)

El disco de Render (y similares) es **efímero**: se borra en cada redeploy/restart.

Por eso:

1. Al **arrancar** se restaura `~/workspace` desde el último snapshot de PostgreSQL.
2. Cada ~2 min se guarda un snapshot comprimido en la DB.
3. Solo se guardan los **últimos 3 snapshots** → no llena la base de datos.
4. El workspace local se limpia y se vuelve a generar desde la DB.

### ¿De dónde saco la base de datos gratis?

Cualquier PostgreSQL con `DATABASE_URL`:

| Proveedor | Enlace | Notas |
|-----------|--------|-------|
| **Neon** (recomendado) | https://neon.tech | Free tier generoso, serverless |
| **Supabase** | https://supabase.com | Free tier + dashboard |
| **Render Postgres** | https://render.com | Free tier (puede dormir) |
| **Aiven / ElephantSQL** | — | Otros free tiers |

1. Crea un proyecto → copia la connection string (empieza por `postgres://` o `postgresql://`).
2. En Render → Environment → añade:
   - `DATABASE_URL` = esa URL
   - `BEBO_TOKEN` = un secreto tuyo (ej. `openssl rand -hex 24`)

## Proxy de puertos (sin custom domain de pago)

Cualquier app que escuche en un puerto del contenedor se expone así:

```text
https://TU_SERVICIO.onrender.com/p/8000/
```

Ejemplo en la terminal:

```bash
cd ~/workspace/mi-proyecto
python3 -m http.server 8000
# o: node server.js  (PORT=3000)
```

Luego en el panel **Puertos web** aparece el link, o abrís `/p/8000/` a mano.

## Sonidos

- Error / éxito / instalación (`npm install`, `pip`, etc.)
- **Silenciar** en ⚙ → “Silenciar sonidos”

## Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `BEBO_TOKEN` / `AUTH_TOKEN` | Token de acceso (recomendado) |
| `DATABASE_URL` | PostgreSQL → restore + autosave (max 3 snapshots) |
| `PORT` | Puerto del servidor (8080) |
| `RENDER_EXTERNAL_HOSTNAME` | Keep-alive free tier |
| `AUTOSAVE_INTERVAL` | Segundos entre snapshots (default 120) |

## Arranque local

```bash
npm install
export BEBO_TOKEN=secreto
# opcional: export DATABASE_URL=postgres://...
node server.js
```
