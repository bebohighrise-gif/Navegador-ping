# Navegador-ping · Escritorio Linux visual (noVNC)

Entorno de escritorio Linux ligero (Alpine + XFCE) accesible desde el navegador.
Optimizado para la **capa gratuita de Render** (512 MB RAM).

## Importante: persistencia

El disco del contenedor es **efímero**. Todo se borra cuando el servicio se reinicia o se duerme.

**Solución:** se usa la base de datos PostgreSQL (`DATABASE_URL`) para no perder nada.

### Comandos de persistencia (dentro de la terminal del escritorio)

```bash
db init                  # Crea las tablas (hazlo una sola vez)
db save archivo.py       # Guarda un archivo en la BD
db load archivo.py       # Lo recupera
db list                  # Lista lo guardado
db pkg add htop          # Marca un paquete para que se reinstale solo
db restore               # Reinstala paquetes + recupera todos los archivos
```

## Deploy en Render

1. Web Service desde este repositorio
2. Añade el add-on **PostgreSQL** (inyecta `DATABASE_URL`)
3. Puerto del servicio: **6080**
4. Listo

Abre la URL de tu servicio → escritorio XFCE en el navegador.

Usuario por defecto: `alpine` / contraseña: `alpine` (si pide alguna).

## Imagen base

`novaspirit/alpine_xfce4_novnc` — Alpine + XFCE + noVNC, muy ligera.

## Licencia

Privado / UNLICENSED.
