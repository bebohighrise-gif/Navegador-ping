# Bebo AI · Consola Linux

Consola web con **tmux real**, multi-sesión, explorador de archivos, terminal web, sonidos y CRT.

## Despliegue

La aplicación es autónoma y no requiere complementos externos ni una base de datos para iniciar. El workspace se crea localmente dentro del contenedor.

- **Puerto predeterminado:** `3000`
- **Comando de inicio:** `node server.js`
- **Variable opcional:** `BEBO_TOKEN` o `AUTH_TOKEN` para proteger la consola.
- **Variable opcional:** `PORT` permite cambiar el puerto si la plataforma lo exige.

En SnapDeploy, configura el servicio para exponer el puerto `3000`.

## Proxy de puertos

Cualquier aplicación que escuche en un puerto del contenedor puede abrirse desde el panel de puertos. Por ejemplo:

```bash
cd ~/workspace/mi-proyecto
python3 -m http.server 8000
```

## Sonidos

- Error, éxito e instalación tienen efectos de sonido.
- Se pueden silenciar desde **Ajustes → Silenciar sonidos**.

## Arranque local

```bash
npm install
export PORT=3000
export BEBO_TOKEN=secreto
node server.js
```

El servicio seguirá funcionando aunque no existan variables de base de datos ni complementos de persistencia.
