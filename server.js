const express = require("express");
const http = require("http");
const https = require("https");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { execFileSync, execFile } = require("child_process");
const pty = require("node-pty");
const { WebSocketServer } = require("ws");
const workspaceApi = require("./workspace_api");

const app = express();
const server = http.createServer(app);
app.disable("x-powered-by");
app.use((_req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "SAMEORIGIN");
  res.setHeader("Referrer-Policy", "same-origin");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  next();
});
const authFailures = new Map();
const PORT = Number(process.env.PORT) || 8080;
const HOME = process.env.HOME || "/home/desktop";
const WORKSPACE_ROOT = path.resolve(HOME, "workspace");
const AUTH_TOKEN = process.env.BEBO_TOKEN || process.env.AUTH_TOKEN || "";
const DEFAULT_SESSION = process.env.TMUX_SESSION_NAME || "bebo";

// ---------------------------------------------------------------
// Helpers de sesión tmux (REALES, no simuladas)
// ---------------------------------------------------------------
function listTmuxSessions() {
  try {
    const out = execFileSync("tmux", ["list-sessions", "-F", "#{session_name}|#{session_created}|#{session_attached}|#{session_windows}"], {
      encoding: "utf8",
      timeout: 5000,
    });
    return out
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => {
        const [name, created, attached, windows] = line.split("|");
        return {
          name,
          created: Number(created) || 0,
          attached: attached === "1",
          windows: Number(windows) || 1,
        };
      });
  } catch (_) {
    return [];
  }
}

function sessionExists(name) {
  return listTmuxSessions().some((s) => s.name === name);
}

function sanitizeSessionName(name) {
  const cleaned = String(name || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "-")
    .replace(/-+/g, "-")
    .slice(0, 32);
  return cleaned || null;
}

function buildWelcomeCmd(sessionName) {
  const authLine = AUTH_TOKEN
    ? 'printf "\\033[32m  [+] auth token ON\\n"'
    : 'printf "\\033[1;31m  [!] auth abierta\\n"';
  return [
    "clear",
    'printf "\\033[1;32m╭──────────────────────────────────────────────╮\\n"',
    'printf "\\033[1;32m│                                              │\\n"',
    'printf "\\033[1;32m│   ██████╗ ███████╗██████╗  ██████╗           │\\n"',
    'printf "\\033[1;32m│   ██╔══██╗██╔════╝██╔══██╗██╔═══██╗          │\\n"',
    'printf "\\033[1;32m│   ██████╔╝█████╗  ██████╔╝██║   ██║          │\\n"',
    'printf "\\033[1;32m│   ██╔══██╗██╔══╝  ██╔══██╗██║   ██║          │\\n"',
    'printf "\\033[1;32m│   ██████╔╝███████╗██████╔╝╚██████╔╝          │\\n"',
    'printf "\\033[1;32m│   ╚═════╝ ╚══════╝╚═════╝  ╚═════╝           │\\n"',
    'printf "\\033[1;32m│                                              │\\n"',
    'printf "\\033[1;32m│            A I   ·   W O R K S P A C E       │\\n"',
    'printf "\\033[1;32m│                                              │\\n"',
    'printf "\\033[1;32m╰──────────────────────────────────────────────╯\\033[0m\\n\\n"',
    'printf "\\033[32m  sesión   \\033[0m' + sessionName + '\\n"',
    'printf "\\033[32m  estado   \\033[0mconectado · tmux real\\n"',
    authLine,
    'printf "\\033[32m  ruta     \\033[0m~/workspace\\n\\n"',
    // Prompt limpio: solo "workspace $ " (sin hostname ni números largos)
    "export PS1='\\[\\033[1;32m\\]workspace\\[\\033[0m\\] $ '",
    "cd ~/workspace 2>/dev/null || true",
    "exec bash --noprofile --norc",
  ].join(" && ");
}

function ensureDefaultSession() {
  if (sessionExists(DEFAULT_SESSION)) return;
  try {
    execFileSync("tmux", ["new-session", "-d", "-s", DEFAULT_SESSION, "-c", path.join(HOME, "workspace"), buildWelcomeCmd(DEFAULT_SESSION)], { timeout: 10000 });
    execFileSync("tmux", ["set-option", "-t", DEFAULT_SESSION, "status", "off"], { timeout: 5000 });
    console.log(`[tmux] sesión permanente creada: ${DEFAULT_SESSION}`);
  } catch (err) {
    console.warn(`[tmux] no se pudo crear ${DEFAULT_SESSION}:`, err.message);
  }
}

// Borrar referencia de proyecto en DB (si hay DATABASE_URL)
function purgeProjectFromDb(projectName) {
  const dbUrl = process.env.DATABASE_URL;
  if (!dbUrl || !projectName) return;
  try {
    // Marca/elimina snapshots que mencionen el proyecto (best-effort)
    execFile(
      "python3",
      [
        "-c",
        `
import os, sys
try:
    import psycopg2
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    raw_url = os.environ["DATABASE_URL"]
    parts = urlsplit(raw_url)
    clean_query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != "uselibpqcompat"]
    db_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(clean_query), parts.fragment))
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bebo_deleted (
            path TEXT PRIMARY KEY,
            deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(
        "INSERT INTO bebo_deleted (path) VALUES (%s) ON CONFLICT (path) DO UPDATE SET deleted_at = NOW()",
        (sys.argv[1],)
    )
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print("db purge skip:", e, file=sys.stderr)
`,
        projectName,
      ],
      { env: process.env, timeout: 10000 },
      () => {}
    );
  } catch (_) {}
}

app.use(express.json({ limit: "4mb" }));

// Si viene token válido por query, setear cookie para /p/*
app.use((req, res, next) => {
  if (AUTH_TOKEN && req.query && req.query.token === AUTH_TOKEN) {
    setAuthCookie(res, AUTH_TOKEN);
  }
  next();
});


app.use(express.raw({ type: "application/zip", limit: "50mb" }));
app.use(express.static(path.join(__dirname, "public")));

function extractToken(req) {
  const header = req.headers["authorization"] || "";
  if (header.startsWith("Bearer ")) return header.slice(7).trim();
  if (req.query && typeof req.query.token === "string") return req.query.token;
  if (req.headers["x-bebo-token"]) return String(req.headers["x-bebo-token"]);
  // Cookie (para /p/PORT y assets sin query token)
  const cookie = req.headers.cookie || "";
  const m = cookie.match(/(?:^|;\s*)bebo_token=([^;]+)/);
  if (m) return decodeURIComponent(m[1]);
  return "";
}

function setAuthCookie(res, token) {
  if (!token) return;
  res.setHeader(
    "Set-Cookie",
    "bebo_token=" + encodeURIComponent(token) + "; Path=/; SameSite=Lax; HttpOnly"
  );
}

function requireAuth(req, res, next) {
  if (!AUTH_TOKEN) return next();
  const token = extractToken(req);
  if (token && token === AUTH_TOKEN) return next();
  const ip = req.ip || req.socket.remoteAddress || "unknown";
  const now = Date.now();
  const failures = (authFailures.get(ip) || []).filter((stamp) => now - stamp < 60000);
  failures.push(now);
  authFailures.set(ip, failures);
  if (failures.length > 30) return res.status(429).json({ error: "too_many_attempts" });
  return res.status(401).json({ error: "unauthorized" });
}

app.get("/healthz", (_req, res) => {
  res.status(200).type("text").send("ok");
});

app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.get("/api/auth-status", (_req, res) => {
  res.json({ required: Boolean(AUTH_TOKEN) });
});

app.get("/api/system", requireAuth, (_req, res) => {
  const memory = process.memoryUsage();
  res.json({
    uptime: Math.floor(process.uptime()),
    rss: memory.rss,
    heap: memory.heapUsed,
    load: os.loadavg()[0] || 0,
    cpus: os.cpus().length,
  });
});

// ---- Sesiones tmux ----
app.get("/api/sessions", requireAuth, (_req, res) => {
  res.json({ sessions: listTmuxSessions(), default: DEFAULT_SESSION });
});

app.post("/api/sessions", requireAuth, (req, res) => {
  const name = sanitizeSessionName(req.body && req.body.name);
  if (!name) return res.status(400).json({ error: "invalid_name" });
  if (sessionExists(name)) return res.status(409).json({ error: "already_exists" });
  try {
    // Crear sesión detached (no altera las otras)
    execFileSync(
      "tmux",
      [
        "new-session",
        "-d",
        "-s",
        name,
        "-c",
        path.join(HOME, "workspace"),
        buildWelcomeCmd(name),
      ],
      { timeout: 10000 }
    );
    // La interfaz ya tiene su propia barra de estado; ocultar la de tmux evita
    // duplicar el nombre de sesión, contador de ventana y fecha en la terminal.
    execFileSync("tmux", ["set-option", "-t", name, "status", "off"], { timeout: 5000 });
    res.json({ ok: true, name });
  } catch (err) {
    res.status(500).json({ error: "create_failed", message: err.message });
  }
});

app.post("/api/sessions/kill", requireAuth, (req, res) => {
  const name = sanitizeSessionName(req.body && req.body.name);
  if (!name) return res.status(400).json({ error: "invalid_name" });
  if (name === DEFAULT_SESSION) {
    return res.status(400).json({ error: "cannot_kill_default" });
  }
  try {
    if (process.env.DATABASE_URL) {
      try {
        execFileSync("python3", ["/usr/local/bin/purge_session.py", name], {
          env: process.env,
          timeout: 15000,
          stdio: "pipe",
        });
      } catch (dbErr) {
        console.error("session db purge failed:", dbErr.message);
        return res.status(500).json({ error: "session_deleted_db_purge_failed", name });
      }
    }
    try {
      execFileSync("tmux", ["kill-session", "-t", name], { timeout: 5000, stdio: "pipe" });
    } catch (tmuxErr) {
      const message = String(tmuxErr.stderr || tmuxErr.message || "");
      const socketGone = /no such file or directory|no server running|session not found/i.test(message);
      if (!socketGone) throw tmuxErr;
      console.warn(`tmux session ${name} already absent; treating as deleted`);
    }
    res.json({ ok: true, name });
  } catch (err) {
    res.status(500).json({ error: "kill_failed", message: err.message });
  }
});

// ---- Explorador ----
app.get("/api/projects", requireAuth, (_req, res) => {
  try {
    res.json({ workspace: "~/workspace", projects: workspaceApi.listProjects(WORKSPACE_ROOT) });
  } catch (err) {
    res.status(500).json({ error: "internal_error" });
  }
});

app.get("/api/git-status", requireAuth, (req, res) => {
  const project = String(req.query.project || "").replace(/[^a-zA-Z0-9._-]/g, "");
  if (!project) return res.status(400).json({ error: "invalid_project" });
  const cwd = path.join(WORKSPACE_ROOT, project);
  try {
    const branch = execFileSync("git", ["-C", cwd, "branch", "--show-current"], { encoding: "utf8", timeout: 5000 }).trim() || "detached";
    const porcelain = execFileSync("git", ["-C", cwd, "status", "--porcelain"], { encoding: "utf8", timeout: 5000 });
    res.json({ branch, changes: porcelain.split("\n").filter(Boolean).length, clean: !porcelain.trim() });
  } catch (_) {
    res.json({ branch: null, changes: 0, clean: true, available: false });
  }
});

app.get("/api/tree", requireAuth, (req, res) => {
  const relPath = typeof req.query.path === "string" ? req.query.path : "";
  const result = workspaceApi.listDirectory(WORKSPACE_ROOT, relPath);
  if (result.error) {
    return res.status(result.error === "not_found" ? 404 : 400).json(result);
  }
  res.json(result);
});

app.get("/api/file", requireAuth, (req, res) => {
  const relPath = typeof req.query.path === "string" ? req.query.path : "";
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const result = workspaceApi.readFilePreview(WORKSPACE_ROOT, relPath);
  if (result.error) {
    return res.status(result.error === "not_found" ? 404 : 400).json(result);
  }
  res.json(result);
});

app.post("/api/create", requireAuth, (req, res) => {
  const { path: relPath, type, content } = req.body || {};
  if (!relPath || !type) return res.status(400).json({ error: "path_and_type_required" });
  if (type !== "file" && type !== "dir") return res.status(400).json({ error: "invalid_type" });
  const result = workspaceApi.createEntry(WORKSPACE_ROOT, relPath, type, content);
  if (result.error) {
    return res.status(result.error === "already_exists" ? 409 : 400).json(result);
  }
  res.json(result);
});

app.post("/api/rename", requireAuth, (req, res) => {
  const { from, to } = req.body || {};
  if (!from || !to) return res.status(400).json({ error: "from_and_to_required" });
  const result = workspaceApi.renameEntry(WORKSPACE_ROOT, from, to);
  if (result.error) {
    const status = result.error === "not_found" ? 404 : result.error === "already_exists" ? 409 : 400;
    return res.status(status).json(result);
  }
  res.json(result);
});

app.post("/api/delete", requireAuth, (req, res) => {
  const { path: relPath } = req.body || {};
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const result = workspaceApi.deleteEntry(WORKSPACE_ROOT, relPath);
  if (result.error) {
    return res.status(result.error === "not_found" ? 404 : 400).json(result);
  }
  // Sincronizar con DB: registrar borrado
  const top = relPath.split("/")[0];
  purgeProjectFromDb(top);
  res.json(result);
});

app.post("/api/write", requireAuth, (req, res) => {
  const { path: relPath, content } = req.body || {};
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const result = workspaceApi.writeFileContent(WORKSPACE_ROOT, relPath, content ?? "");
  if (result.error) return res.status(400).json(result);
  res.json(result);
});

// ---- Upload (multipart simple via base64 o raw) ----
app.post("/api/upload", requireAuth, express.raw({ type: "*/*", limit: "50mb" }), (req, res) => {
  const relPath = typeof req.query.path === "string" ? req.query.path : "";
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const buf = Buffer.isBuffer(req.body) ? req.body : Buffer.from(req.body || "");
  if (!buf.length) return res.status(400).json({ error: "empty_body" });

  // Si es .zip y extract=1, descomprimir
  if (req.query.extract === "1" || relPath.toLowerCase().endsWith(".zip")) {
    const dest = typeof req.query.dest === "string" ? req.query.dest : path.dirname(relPath);
    const result = workspaceApi.extractZip(WORKSPACE_ROOT, dest, buf);
    if (result.error) return res.status(400).json(result);
    return res.json(result);
  }

  const result = workspaceApi.saveUploadedFile(WORKSPACE_ROOT, relPath, buf);
  if (result.error) return res.status(400).json(result);
  res.json(result);
});

// Upload con JSON base64 (más simple desde el frontend)
app.post("/api/upload-b64", requireAuth, (req, res) => {
  const { path: relPath, content_b64, extract, dest } = req.body || {};
  if (!relPath || !content_b64) return res.status(400).json({ error: "path_and_content_required" });
  let buf;
  try {
    buf = Buffer.from(content_b64, "base64");
  } catch (_) {
    return res.status(400).json({ error: "invalid_base64" });
  }
  if (extract || relPath.toLowerCase().endsWith(".zip")) {
    const d = dest || path.dirname(relPath);
    const result = workspaceApi.extractZip(WORKSPACE_ROOT, d, buf);
    if (result.error) return res.status(400).json(result);
    return res.json(result);
  }
  const result = workspaceApi.saveUploadedFile(WORKSPACE_ROOT, relPath, buf);
  if (result.error) return res.status(400).json(result);
  res.json(result);
});

// ---- Download ----
app.get("/api/download", requireAuth, (req, res) => {
  const relPath = typeof req.query.path === "string" ? req.query.path : "";
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const result = workspaceApi.readFileForDownload(WORKSPACE_ROOT, relPath);
  if (result.error) {
    return res.status(result.error === "not_found" ? 404 : 400).json(result);
  }
  res.setHeader("Content-Type", "application/octet-stream");
  res.setHeader("Content-Disposition", `attachment; filename="${result.name}"`);
  res.send(result.buffer);
});

app.get("/api/download-zip", requireAuth, (req, res) => {
  const relPath = typeof req.query.path === "string" ? req.query.path : "";
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const result = workspaceApi.createZipBuffer(WORKSPACE_ROOT, relPath);
  if (result.error) {
    return res.status(result.error === "not_found" ? 404 : 400).json(result);
  }
  res.setHeader("Content-Type", "application/zip");
  res.setHeader("Content-Disposition", `attachment; filename="${result.name}"`);
  res.send(result.buffer);
});

// ---- Logs del proyecto ----
app.get("/api/logs", requireAuth, (req, res) => {
  const project = typeof req.query.project === "string" ? req.query.project : "";
  if (!project) return res.status(400).json({ error: "project_required" });
  const result = workspaceApi.findProjectLogs(WORKSPACE_ROOT, project);
  if (result.error) return res.status(404).json(result);
  res.json(result);
});

app.get("/api/log-tail", requireAuth, (req, res) => {
  const relPath = typeof req.query.path === "string" ? req.query.path : "";
  if (!relPath) return res.status(400).json({ error: "path_required" });
  const result = workspaceApi.tailLog(WORKSPACE_ROOT, relPath);
  if (result.error) {
    return res.status(result.error === "not_found" ? 404 : 400).json(result);
  }
  res.json(result);
});

// Capture pane de la sesión tmux (log de la terminal en vivo)
app.get("/api/session-log", requireAuth, (req, res) => {
  const name = sanitizeSessionName(req.query.session) || DEFAULT_SESSION;
  try {
    const out = execFileSync(
      "tmux",
      ["capture-pane", "-t", name, "-p", "-S", "-200"],
      { encoding: "utf8", timeout: 5000 }
    );
    res.json({ session: name, content: out });
  } catch (err) {
    res.status(404).json({ error: "session_not_found", message: err.message });
  }
});


// ---------------------------------------------------------------
// Proxy de puertos locales (apps web de los proyectos)
// Sin dominio de pago: https://TU_HOST/p/3000/ → localhost:3000
// ---------------------------------------------------------------
const ALLOWED_PORT_MIN = 1024;
const ALLOWED_PORT_MAX = 65535;
const BLOCKED_PORTS = new Set([PORT, 22, 25, 5432]); // no proxy al propio server ni servicios sensibles

function parseProxyPort(raw) {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < ALLOWED_PORT_MIN || n > ALLOWED_PORT_MAX) return null;
  if (BLOCKED_PORTS.has(n)) return null;
  return n;
}

/** Lista puertos en LISTEN (best-effort, Linux) */
function listListeningPorts() {
  try {
    const out = execFileSync("sh", ["-c", "ss -tlnH 2>/dev/null || netstat -tln 2>/dev/null || true"], {
      encoding: "utf8",
      timeout: 5000,
    });
    const ports = new Set();
    for (const line of out.split("\n")) {
      const m = line.match(/(?:[:.]|\s)(\d{4,5})\s+.*LISTEN/i) || line.match(/:(\d{4,5})\s/);
      if (m) {
        const p = Number(m[1]);
        if (p >= ALLOWED_PORT_MIN && p <= ALLOWED_PORT_MAX && !BLOCKED_PORTS.has(p)) ports.add(p);
      }
    }
    return Array.from(ports).sort((a, b) => a - b);
  } catch (_) {
    return [];
  }
}

app.get("/api/ports", requireAuth, (_req, res) => {
  res.json({ ports: listListeningPorts() });
});

// Proxy middleware for /p/:port/*
app.use("/p/:port", requireAuth, (req, res, next) => {
  const port = parseProxyPort(req.params.port);
  if (!port) return res.status(400).json({ error: "invalid_port" });
  const target = `http://127.0.0.1:${port}`;
  const url = new URL(req.url, target);
  const opts = {
    hostname: "127.0.0.1",
    port,
    path: url.pathname + url.search,
    method: req.method,
    headers: { ...req.headers, host: `127.0.0.1:${port}` },
  };
  delete opts.headers["host"];
  opts.headers.host = `127.0.0.1:${port}`;
  const proxyReq = http.request(opts, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });
  proxyReq.on("error", (err) => {
    res.status(502).json({ error: "proxy_error", message: err.message });
  });
  req.pipe(proxyReq);
});

// ---------------------------------------------------------------
// WebSocket terminal (tmux attach)
// ---------------------------------------------------------------
const wss = new WebSocketServer({ server, path: "/ws" });

wss.on("connection", (ws, req) => {
  const url = new URL(req.url, "http://localhost");
  const session = sanitizeSessionName(url.searchParams.get("session")) || DEFAULT_SESSION;
  const token = url.searchParams.get("token") || "";
  if (AUTH_TOKEN && token !== AUTH_TOKEN) {
    ws.close(4001, "unauthorized");
    return;
  }

  ensureDefaultSession();
  if (!sessionExists(session)) {
    try {
      execFileSync("tmux", ["new-session", "-d", "-s", session, "-c", path.join(HOME, "workspace"), buildWelcomeCmd(session)], { timeout: 10000 });
      execFileSync("tmux", ["set-option", "-t", session, "status", "off"], { timeout: 5000 });
    } catch (err) {
      ws.close(1011, "session_create_failed");
      return;
    }
  }

  let cols = 80;
  let rows = 24;
  const term = pty.spawn("tmux", ["attach-session", "-t", session], {
    name: "xterm-256color",
    cols,
    rows,
    cwd: path.join(HOME, "workspace"),
    env: { ...process.env, TERM: "xterm-256color", COLORTERM: "truecolor" },
  });

  term.onData((data) => {
    if (ws.readyState === 1) ws.send(data);
  });
  term.onExit(() => {
    try { ws.close(); } catch (_) {}
  });

  ws.on("message", (msg) => {
    try {
      const data = JSON.parse(msg.toString());
      if (data.type === "input" && typeof data.data === "string") {
        term.write(data.data);
      } else if (data.type === "resize") {
        cols = Math.max(20, Math.min(500, Number(data.cols) || cols));
        rows = Math.max(5, Math.min(200, Number(data.rows) || rows));
        term.resize(cols, rows);
      }
    } catch (_) {
      // raw binary / string fallback
      if (typeof msg === "string" || Buffer.isBuffer(msg)) term.write(msg);
    }
  });

  ws.on("close", () => {
    try { term.kill(); } catch (_) {}
  });
});

// Start
ensureDefaultSession();
server.listen(PORT, "0.0.0.0", () => {
  console.log(`[bebo] listening on :${PORT}`);
});
