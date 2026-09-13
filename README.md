# Bebo AI · Consola Linux

Consola web hacker con **tmux real**, multi-sesión, explorador, proxy de puertos (sin pagar dominio), sonidos y CRT.

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

No hace falta pagar subdominio en Render: el proxy va por path en el mismo host.

## Sonidos

- Error / éxito / instalación (`npm install`, `pip`, etc.)
- **Silenciar** en ⚙ → “Silenciar sonidos”

## Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `BEBO_TOKEN` / `AUTH_TOKEN` | Token (recomendado) |
| `DATABASE_URL` | PostgreSQL autosave + borrados |
| `PORT` | Puerto del servidor (8080) |
| `RENDER_EXTERNAL_HOSTNAME` | Keep-alive free tier |

## Arranque local

```bash
npm install
export BEBO_TOKEN=secreto
node server.js
```
