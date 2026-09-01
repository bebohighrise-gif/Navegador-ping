const dns = require('dns');
dns.setDefaultResultOrder('ipv4first');

const http = require('http');
const httpProxy = require('http-proxy');
const { Client } = require('pg');

const PORT = process.env.PORT || 8080;
const DATABASE_URL = process.env.DATABASE_URL;

const proxy = httpProxy.createProxyServer({ ws: true });
const routeTable = {};

async function initDB(client) {
    const createTableQuery = `
        CREATE TABLE IF NOT EXISTS proyectos (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(100) UNIQUE NOT NULL,
            subdominio VARCHAR(150) UNIQUE NOT NULL,
            puerto INT NOT NULL,
            comando VARCHAR(255) NOT NULL,
            estado VARCHAR(20) DEFAULT 'activo',
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    `;
    await client.query(createTableQuery);
}

async function refreshRoutes() {
    if (!DATABASE_URL) return;
    let client;
    try {
        client = new Client({ 
            connectionString: DATABASE_URL, 
            ssl: { rejectUnauthorized: false } 
        });
        await client.connect();
        await initDB(client);

        const res = await client.query("SELECT subdominio, puerto FROM proyectos WHERE estado = 'activo'");
        
        Object.keys(routeTable).forEach(key => delete routeTable[key]);
        res.rows.forEach(row => {
            routeTable[row.subdominio.toLowerCase()] = row.puerto;
        });
    } catch (err) {
        console.error('[ROUTER] Error al sincronizar rutas desde la DB:', err.message);
    } finally {
        if (client) await client.end();
    }
}

refreshRoutes();
setInterval(refreshRoutes, 10000);

const server = http.createServer((req, res) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    const targetPort = routeTable[host];

    if (targetPort) {
        proxy.web(req, res, { target: `http://127.0.0.1:${targetPort}` }, (err) => {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: '502 Bad Gateway', details: 'El proyecto no está respondiendo en el puerto asignado.' }));
        });
    } else {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(`
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="UTF-8">
                <title>Bebo AI - Engine Host</title>
                <style>
                    body { font-family: monospace; background: #0d1117; color: #c9d1d9; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #161b22; padding: 2rem; border-radius: 8px; border: 1px solid #30363d; max-width: 500px; }
                    h1 { color: #58a6ff; font-size: 1.5rem; margin: 0 0 1rem 0; }
                    code { background: #21262d; color: #79c0ff; padding: 2px 6px; border-radius: 4px; }
                    .status { display: inline-block; width: 10px; height: 10px; background: #238636; border-radius: 50%; margin-right: 6px; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1><span class="status"></span>Bebo AI Engine Active</h1>
                    <p>Host: <code>${host}</code></p>
                    <p>WebSocket PTY disponible en <code>wss://${host}</code></p>
                </div>
            </body>
            </html>
        `);
    }
});

// Manejo de WebSockets (Enrutamiento o redirección al Servidor PTY de Python en puerto 8765)
server.on('upgrade', (req, socket, head) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    const targetPort = routeTable[host];

    if (targetPort) {
        proxy.ws(req, socket, head, { target: `ws://127.0.0.1:${targetPort}` });
    } else {
        // Redirigir WebSocket PTY principal al puerto interno donde corre server.py (8765)
        proxy.ws(req, socket, head, { target: `ws://127.0.0.1:8765` }, (err) => {
            socket.destroy();
        });
    }
});

server.listen(PORT, () => {
    console.log(`[ROUTER] Escuchando en el puerto ${PORT}`);
});
