const dns = require('dns');
dns.setDefaultResultOrder('ipv4first'); // Forza IPv4 para evitar errores ENETUNREACH

const http = require('http');
const httpProxy = require('http-proxy');
const { Client } = require('pg');

const PORT = process.env.PORT || 8080;
const DATABASE_URL = process.env.DATABASE_URL;

const proxy = httpProxy.createProxyServer({ ws: true });
const routeTable = {};

// Crea la tabla 'proyectos' automáticamente si no existe
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
        
        // Limpia y sincroniza la tabla de enrutamiento
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

// Sincroniza rutas al iniciar y cada 10 segundos
refreshRoutes();
setInterval(refreshRoutes, 10000);

const server = http.createServer((req, res) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    const targetPort = routeTable[host];

    if (targetPort) {
        // Redirige al puerto interno del proyecto alojado
        proxy.web(req, res, { target: `http://127.0.0.1:${targetPort}` }, (err) => {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: '502 Bad Gateway', details: 'El proyecto asignado a este puerto no está respondiendo.' }));
        });
    } else {
        // Respuesta visual de bienvenida para el dominio principal sin subdominio registrado
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(`
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Bebo AI - Engine Host</title>
                <style>
                    body { font-family: monospace; background-color: #0d1117; color: #c9d1d9; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #161b22; padding: 2rem; border-radius: 8px; border: 1px solid #30363d; box-shadow: 0 4px 12px rgba(0,0,0,0.5); max-width: 500px; width: 100%; }
                    h1 { color: #58a6ff; font-size: 1.5rem; margin-top: 0; }
                    p { font-size: 0.9rem; line-height: 1.5; color: #8b949e; }
                    code { background: #21262d; color: #79c0ff; padding: 2px 6px; border-radius: 4px; }
                    .status { display: inline-block; width: 10px; height: 10px; background-color: #238636; border-radius: 50%; margin-right: 6px; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1><span class="status"></span>Bebo AI Kernel Host</h1>
                    <p>Servidor proxy y entorno de ejecución activos.</p>
                    <p>Host actual solicitado: <code>${host}</code></p>
                    <p>El WebSocket PTY de la terminal y la sincronización con la base de datos están operando correctamente.</p>
                </div>
            </body>
            </html>
        `);
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
