const dns = require('dns');
dns.setDefaultResultOrder('ipv4first');

const http = require('http');
const httpProxy = require('http-proxy');
const { Pool } = require('pg');

// ============================================================
// CONFIGURACIÓN
// ============================================================
const PORT = process.env.PORT || 8080;
const DATABASE_URL = process.env.DATABASE_URL;
const DEFAULT_WS_PORT = parseInt(process.env.DEFAULT_WS_PORT || '8765', 10);
const ALLOW_DEFAULT_WS_HOSTS = (process.env.ALLOW_DEFAULT_WS_HOSTS || '')
    .split(',')
    .map(h => h.trim().toLowerCase())
    .filter(Boolean);

// ============================================================
// TABLA DE RUTAS (en memoria)
// ============================================================
let routeTable = new Map(); // key: subdominio, value: { puerto, updated_at }

// ============================================================
// POOL DE CONEXIONES A POSTGRESQL
// ============================================================
let pool = null;
if (DATABASE_URL) {
    pool = new Pool({
        connectionString: DATABASE_URL,
        ssl: { rejectUnauthorized: false }, // En producción usar certificados válidos
        max: 10,
        idleTimeoutMillis: 30000,
    });
}

// ============================================================
// INICIALIZACIÓN DE LA BD
// ============================================================
async function initDB() {
    if (!pool) return;
    const client = await pool.connect();
    try {
        await client.query(`
            CREATE TABLE IF NOT EXISTS proyectos (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100) UNIQUE NOT NULL,
                subdominio VARCHAR(150) UNIQUE NOT NULL,
                puerto INT NOT NULL,
                comando VARCHAR(255) NOT NULL,
                estado VARCHAR(20) DEFAULT 'activo',
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        `);
        await client.query(`
            CREATE INDEX IF NOT EXISTS idx_proyectos_subdominio_estado 
            ON proyectos(subdominio, estado);
        `);
        await client.query(`
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        `);
        await client.query(`
            DROP TRIGGER IF EXISTS update_proyectos_updated_at ON proyectos;
            CREATE TRIGGER update_proyectos_updated_at
            BEFORE UPDATE ON proyectos
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
        `);
    } finally {
        client.release();
    }
}

// ============================================================
// REFRESCO DE RUTAS (con comparación para evitar cambios innecesarios)
// ============================================================
let refreshing = false;

async function refreshRoutes() {
    if (!pool) return;
    if (refreshing) {
        console.log('[ROUTER] Ya hay un refresco en curso, omitiendo...');
        return;
    }
    refreshing = true;
    try {
        const client = await pool.connect();
        try {
            const res = await client.query(
                "SELECT subdominio, puerto, updated_at FROM proyectos WHERE estado = 'activo'"
            );
            const newRoutes = new Map();
            res.rows.forEach(row => {
                newRoutes.set(row.subdominio.toLowerCase(), {
                    puerto: row.puerto,
                    updated_at: row.updated_at || new Date(0)
                });
            });

            // Detectar cambios (comparar tamaño y contenido)
            let changed = false;
            if (routeTable.size !== newRoutes.size) {
                changed = true;
            } else {
                for (const [key, value] of newRoutes) {
                    const old = routeTable.get(key);
                    if (!old || old.puerto !== value.puerto) {
                        changed = true;
                        break;
                    }
                }
            }

            if (changed) {
                routeTable = newRoutes;
                console.log(`[ROUTER] Rutas actualizadas: ${routeTable.size} entradas.`);
            } else {
                console.log('[ROUTER] No hubo cambios en las rutas.');
            }
        } finally {
            client.release();
        }
    } catch (err) {
        console.error('[ROUTER] Error al refrescar rutas:', err.message);
    } finally {
        refreshing = false;
    }
}

// ============================================================
// PROXY HTTP/WS
// ============================================================
const proxy = httpProxy.createProxyServer({
    ws: true,
    timeout: 10000,          // tiempo de espera para conectar al destino
    proxyTimeout: 10000,
    xfwd: true,              // pasa headers originales (X-Forwarded-*)
});

proxy.on('error', (err, req, res) => {
    console.error('[PROXY] Error:', err.message);
    if (res && !res.headersSent && typeof res.writeHead === 'function') {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Bad Gateway', details: err.message }));
    } else if (res && typeof res.destroy === 'function') {
        res.destroy();
    } else if (req && typeof req.destroy === 'function') {
        req.destroy();
    }
});

// ============================================================
// SERVIDOR HTTP
// ============================================================
const server = http.createServer((req, res) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    // Validación básica del host
    if (!/^[a-z0-9\-\.]+$/.test(host)) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ error: 'Invalid host header' }));
    }

    const route = routeTable.get(host);
    if (route) {
        proxy.web(req, res, { target: `http://127.0.0.1:${route.puerto}` });
    } else {
        // Página por defecto
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(`
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="UTF-8">
                <title>Bebo AI - Engine Host</title>
                <style>
                    body { font-family: monospace; background-color: #0d1117; color: #c9d1d9; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #161b22; padding: 2rem; border-radius: 8px; border: 1px solid #30363d; max-width: 500px; width: 100%; }
                    h1 { color: #58a6ff; font-size: 1.5rem; margin-top: 0; }
                    p { font-size: 0.9rem; line-height: 1.5; color: #8b949e; }
                    code { background: #21262d; color: #79c0ff; padding: 2px 6px; border-radius: 4px; }
                    .status { display: inline-block; width: 10px; height: 10px; background-color: #238636; border-radius: 50%; margin-right: 6px; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1><span class="status"></span>Bebo AI Kernel Host</h1>
                    <p>Servidor activo. Host actual: <code>${host}</code></p>
                    <p>Este dominio no está registrado. Contacta con el administrador.</p>
                </div>
            </body>
            </html>
        `);
    }
});

// ============================================================
// MANEJO DE WEBSOCKETS (upgrade)
// ============================================================
server.on('upgrade', (req, socket, head) => {
    const host = (req.headers.host || '').split(':')[0].toLowerCase();
    if (!/^[a-z0-9\-\.]+$/.test(host)) {
        socket.write('HTTP/1.1 400 Bad Request\r\n\r\n');
        socket.destroy();
        return;
    }

    const route = routeTable.get(host);
    if (route) {
        proxy.ws(req, socket, head, { target: `ws://127.0.0.1:${route.puerto}` });
    } else if (ALLOW_DEFAULT_WS_HOSTS.includes(host)) {
        // Solo para hosts en lista blanca, redirigir al puerto PTY por defecto
        proxy.ws(req, socket, head, { target: `ws://127.0.0.1:${DEFAULT_WS_PORT}` });
    } else {
        socket.write('HTTP/1.1 404 Not Found\r\n\r\n');
        socket.destroy();
    }
});

// ============================================================
// ARRANQUE Y SCHEDULER
// ============================================================
async function startScheduler() {
    if (pool) {
        await initDB();
        await refreshRoutes();
        setInterval(refreshRoutes, 30000); // 30 segundos
    } else {
        console.warn('[ROUTER] DATABASE_URL no definida, las rutas no se actualizarán.');
    }
}

server.listen(PORT, async () => {
    console.log(`[ROUTER] Escuchando en el puerto ${PORT}`);
    await startScheduler();
});

// ============================================================
// APAGADO GRACEFUL
// ============================================================
process.on('SIGTERM', async () => {
    console.log('[ROUTER] Recibida señal SIGTERM, cerrando servidor...');
    server.close(() => {
        console.log('[ROUTER] Servidor HTTP cerrado.');
        if (pool) {
            pool.end().then(() => {
                console.log('[ROUTER] Pool de conexiones cerrado.');
                process.exit(0);
            }).catch(err => {
                console.error('[ROUTER] Error al cerrar pool:', err);
                process.exit(1);
            });
        } else {
            process.exit(0);
        }
    });
});

process.on('SIGINT', () => {
    process.emit('SIGTERM');
});
