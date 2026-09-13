# Bebo AI · Consola Linux

Consola web profesional con shell real (bash), persistencia automática en PostgreSQL y soporte multi-lenguaje.
Optimizada para la capa gratuita de Render (512 MB).

## Características

- Terminal visual tipo PC (xterm.js)
- Shell bash real
- **Autosave** cada 2 minutos → PostgreSQL
- **Restore automático** al arrancar
- Python, Node, PHP, Ruby, Go, Java listos
- Compatible con HTTPS (Render)

## Deploy en Render

1. Web Service desde este repositorio
2. Add-on **PostgreSQL** (inyecta `DATABASE_URL`)
3. **Port** = `8080`
4. Deploy

URL: `https://tu-servicio.onrender.com`

## Persistencia (automática)

| Qué | Comportamiento |
|-----|----------------|
| `~/workspace` | Se guarda solo |
| Configs (`.bashrc`, `.gitconfig`, …) | Se guardan solas |
| `~/.local` (pip --user, etc.) | Se guarda solo |
| Al arrancar | Se restaura todo |

Comandos manuales (opcionales):

```bash
db list
db save archivo.py
db restore
db pkg add htop
```

## Instalar software

```bash
sudo apk add htop ffmpeg     # sistema
pip3 install --user requests # Python
npm install -g typescript    # Node
```

Trabaja siempre dentro de `~/workspace` para no perder nada.

## Variables de entorno

| Variable | Obligatorio | Descripción |
|----------|-------------|-------------|
| `DATABASE_URL` | Sí (recomendado) | Postgres de Render |
| `PORT` | No (default 8080) | Puerto HTTP |
| `AUTOSAVE_INTERVAL` | No (default 120) | Segundos entre guardados |

## Nota sobre Render free

El servicio se duerme tras 15 min sin tráfico. Un ping periódico lo mantiene despierto.
Al despertar, todo se restaura desde la base de datos.

## Licencia

Privado / UNLICENSED.
