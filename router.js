const http = require('http');
const httpProxy = require('http-proxy');
const { Client } = require('pg');

const PORT = process.env.PORT || 8080;
const DATABASE_URL = process.env.DATABASE_URL;

const proxy = httpProxy.createProxyServer({ ws: true });
const routeTable = {};

async function refreshRoutes() {
    if (!DATABASE_URL) return;
    let client;
    try {
        client = new Client({ 
            connectionString: DATABASE_URL, 
            ssl: { rejectUnauthorized: false } 
        });
        await client.connect();
        const res = await client.query("SELECT subdominio, puerto FROM proyectos WHERE estado = 'activo'");
        
        // Limpiar y actualizar la tabla de enrutamiento
        Object.keys(routeTable).forEach(key => delete routeTable[key]);
        res.rows.forEach(row => {
            routeTable[row.subdominio.toLowerCase()] = row.puerto;
        });
    } catch (err) {
        console.error('[ROUTER] Error al sincronizar rutas desde la DB pasedata:', err.message);
    } finally {
        if (client) await client.end();
    }
}

// Sincronizar rutas al iniciar y cada 10 segundos
refreshRoutes();
setInterval(refreshRoutes, 10000);

const server = http.createServer((req, res) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    const targetPort = routeTable[host];

    if (targetPort) {
        proxy.web(req, res, { target: `http://127.0.0.1:${targetPort}` }, (err) => {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: '502 Bad Gateway', details: 'El proyecto asignado a este puerto no está respondiendo.' }));
        });
    } else {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Proyecto no encontrado o inactivo en pasedata', host: host }));
    }
});

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
    console.log(`[ROUTER] Escuchando en el puerto ${PORT}`);
});
