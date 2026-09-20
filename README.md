# Bebo AI · Consola Linux

Consola web hacker con **tmux real**, multi-sesión, explorador, proxy de puertos (sin pagar dominio), sonidos y CRT.

## Persistencia opcional

La aplicación funciona sin PostgreSQL: si `DATABASE_URL` no existe, el workspace local y la terminal siguen funcionando normalmente. En ese modo, los archivos pueden perderse cuando la plataforma reinicia el contenedor.

Si necesitas persistencia entre reinicios, puedes conectar cualquier PostgreSQL mediante `DATABASE_URL`:

| Proveedor | Enlace | Notas |
|-----------|--------|-------|
| **Neon** (recomendado) | https://neon.tech | Free tier generoso, serverless |
| **Supabase** | https://supabase.com | Free tier + dashboard |
| **Render Postgres** | https://render.com | Free tier (puede dormir) |
| **Aiven / ElephantSQL** | — | Otros free tiers |

1. Crea un proyecto y copia la connection string (empieza por `postgres://` o `postgresql://`).
2. En los secretos o variables de entorno de tu plataforma añade:
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
| `DATABASE_URL` | **Opcional**. PostgreSQL para restore + autosave (máximo 3 snapshots) |
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
