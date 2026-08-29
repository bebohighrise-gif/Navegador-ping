const http = require('http');
const httpProxy = require('http-proxy');
const { Client } = require('pg');

const PORT = process.env.PORT || 8080;
const DATABASE_URL = process.env.DATABASE_URL;

const proxy = httpProxy.createProxyServer({ ws: true });

// Caché en memoria de rutas (subdominio -> puerto)
const routeTable = {};

// Cargar mapeo desde PostgreSQL (Neon.tech)
async function refreshRoutes() {
    if (!DATABASE_URL) return;
    try {
        const client = new Client({ connectionString: DATABASE_URL, ssl: { rejectUnauthorized: false } });
        await client.connect();
        const res = await client.query('SELECT subdominio, puerto FROM proyectos WHERE estado = \'activo\'');
        res.rows.forEach(row => {
            routeTable[row.subdominio.toLowerCase()] = row.puerto;
        });
        await client.end();
    } catch (err) {
        console.error('Error al cargar rutas desde la DB:', err.message);
    }
}

// Actualizar la tabla de rutas cada 10 segundos
refreshRoutes();
setInterval(refreshRoutes, 10000);

const server = http.createServer((req, res) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    
    // Si la petición es hacia el subdominio del router/terminal o por IP directa
    const targetPort = routeTable[host];

    if (targetPort) {
        // Enrutar al proyecto interno en su puerto asignado
        proxy.web(req, res, { target: `http://127.0.0.1:${targetPort}` }, (err) => {
            res.writeHead(502, { 'Content-Type': 'text/plain' });
            res.end('Error 502: El proyecto asignado a este puerto no está respondiendo.');
        });
    } else {
        // Respuesta por defecto si el subdominio no existe o no está registrado
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Proyecto no encontrado o inactivo', host: host }));
    }
});

// Soporte para WebSockets de los proyectos alojados
server.on('upgrade', (req, socket, head) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    const targetPort = routeTable[host];

    if (targetPort) {
        proxy.ws(req, socket, head, { target: `ws://127.0.0.1:${targetPort}` });
    } else {
        socket.destroy();
    }
});

server.listen(PORT, () => {
    console.log(`Router de Hosting corriendo en el puerto ${PORT}`);
});
  
