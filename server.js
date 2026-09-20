const express = require("express");
const http = require("http");
const https = require("https");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { execFileSync } = require("child_process");
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
const PORT = Number(process.env.PORT) || 3000;
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
  return [
    "clear",
    // Prompt limpio y real: sin banners, sin ASCII art, sin metadata decorativa.
    "export PS1='\\[\\033[1;37m\\]workspace\\[\\033[0m\\] $ '",
    "cd ~/workspace 2>/dev/null || true",
    "exec bash --noprofile --norc",
  ].join(" && ");
}

// Comando que se inyecta en CADA conexión (nueva o reutilizada) para que el
// prompt sea siempre consistente, incluso si la sesión tmux ya existía desde
// antes con un PS1 distinto (por ejemplo, el prompt largo con el hostname del
// contenedor). Se envía con eco apagado para que no se vea como si alguien
// hubiera tipeado el comando.
function normalizePromptCmd() {
  return "stty -echo 2>/dev/null; export PS1='\\[\\033[1;37m\\]workspace\\[\\033[0m\\] $ '; clear; stty echo 2>/dev/null\r";
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
  // Nota: ya se permite matar también la sesión por defecto ("bebo"). Al
  // reconectar, el WebSocket la vuelve a crear desde cero (tmux new-session
  // -A) con el prompt limpio actual, sin arrastrar estado viejo.
  try {
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
      const m = line.match(/:(\d+)\s/);
      if (m) {
        const port = Number(m[1]);
        if (port >= ALLOWED_PORT_MIN && port <= ALLOWED_PORT_MAX && !BLOCKED_PORTS.has(port)) {
          ports.add(port);
        }
      }
    }
    return [...ports].sort((a, b) => a - b);
  } catch (_) {
    return [];
  }
}

app.get("/api/ports", requireAuth, (_req, res) => {
  res.json({ ports: listListeningPorts(), proxyBase: "/p/" });
});

function proxyToLocal(req, res, port, restPath) {
  const q = req.url.includes("?") ? "?" + req.url.split("?").slice(1).join("?") : "";
  let pathPart = restPath || "/";
  if (!pathPart.startsWith("/")) pathPart = "/" + pathPart;
  const headers = { ...req.headers, host: "127.0.0.1:" + port };
  delete headers["authorization"];
  delete headers["x-bebo-token"];
  delete headers["content-length"];

  const opts = {
    hostname: "127.0.0.1",
    port,
    path: pathPart + q,
    method: req.method,
    headers,
    timeout: 30000,
  };

  const proxyReq = http.request(opts, (proxyRes) => {
    const outHeaders = { ...proxyRes.headers };
    if (outHeaders.location) {
      try {
        const loc = outHeaders.location;
        if (loc.startsWith("http://127.0.0.1") || loc.startsWith("http://localhost")) {
          const u = new URL(loc);
          outHeaders.location = "/p/" + port + u.pathname + u.search;
        } else if (loc.startsWith("/")) {
          outHeaders.location = "/p/" + port + loc;
        }
      } catch (_) {}
    }
    res.writeHead(proxyRes.statusCode || 502, outHeaders);
    proxyRes.pipe(res);
  });

  proxyReq.on("error", (err) => {
    if (!res.headersSent) {
      res.status(502).type("text").send(
        "[bebo proxy] nada escucha en el puerto " + port + "\\n" +
        "Ejemplo: python3 -m http.server " + port + "\\n" +
        "Luego abrí /p/" + port + "/\\n" +
        "detalle: " + err.message + "\\n"
      );
    }
  });

  proxyReq.on("timeout", () => {
    proxyReq.destroy();
    if (!res.headersSent) res.status(504).send("proxy timeout");
  });

  if (req.method === "GET" || req.method === "HEAD") proxyReq.end();
  else req.pipe(proxyReq);
}

// /p/3000  y  /p/3000/ruta...
app.all("/p/:port", requireAuth, (req, res) => {
  const port = parseProxyPort(req.params.port);
  if (!port) return res.status(400).send("puerto no permitido");
  proxyToLocal(req, res, port, "/");
});

app.all("/p/:port/*", requireAuth, (req, res) => {
  const port = parseProxyPort(req.params.port);
  if (!port) return res.status(400).send("puerto no permitido");
  const rest = req.params[0] ? "/" + req.params[0] : "/";
  proxyToLocal(req, res, port, rest);
});


// ---------------------------------------------------------------
// WebSocket — tmux REAL por sesión
// ---------------------------------------------------------------
const wss = new WebSocketServer({ server, path: "/ws" });

wss.on("connection", (ws, req) => {
  if (AUTH_TOKEN) {
    const url = new URL(req.url || "", `http://${req.headers.host}`);
    const token =
      url.searchParams.get("token") ||
      (req.headers["sec-websocket-protocol"] || "").replace(/^token,?\s*/i, "") ||
      "";
    if (token !== AUTH_TOKEN) {
      ws.close(4001, "unauthorized");
      return;
    }
  }

  const url = new URL(req.url || "", `http://${req.headers.host}`);
  let sessionName = sanitizeSessionName(url.searchParams.get("session")) || DEFAULT_SESSION;

  const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress || "?";
  console.log(`[ws] ${ip} → sesión "${sessionName}"`);

  const ptyEnv = {
    ...process.env,
    TERM: "xterm-256color",
    COLORTERM: "truecolor",
    HOME,
    USER: "desktop",
    SHELL: "/bin/bash",
    LANG: "C.UTF-8",
    PATH: process.env.PATH || "/home/desktop/.local/bin:/usr/local/bin:/usr/bin:/bin",
  };
  const ptyOpts = { name: "xterm-256color", cols: 100, rows: 30, cwd: HOME, env: ptyEnv };

  let term;
  try {
    // -A: attach or create — sesión tmux REAL e independiente
    term = pty.spawn(
      "tmux",
      [
        "new-session",
        "-A",
        "-s",
        sessionName,
        "-c",
        path.join(HOME, "workspace"),
        buildWelcomeCmd(sessionName),
      ],
      ptyOpts
    );
    // La UI web reemplaza la barra de tmux; desactivarla también en sesiones
    // antiguas evita que aparezcan contador de ventana, host y fecha duplicados.
    try {
      execFileSync("tmux", ["set-option", "-t", sessionName, "status", "off"], { timeout: 5000 });
    } catch (_) {}
    // Sea sesión nueva o reutilizada, forzar el mismo prompt limpio. Esto es
    // lo que evita el prompt "roto" (hostname largo, sin PS1 custom) que
    // aparecía en sesiones viejas que sobrevivían a un redeploy.
    setTimeout(() => {
      try { term.write(normalizePromptCmd()); } catch (_) {}
    }, 250);
  } catch (err) {
    console.warn("[pty] tmux falló, fallback bash:", err.message);
    try {
      term = pty.spawn("bash", ["-l"], ptyOpts);
    } catch (err2) {
      ws.close();
      return;
    }
  }

  term.onData((data) => {
    if (ws.readyState === ws.OPEN) {
      try { ws.send(data); } catch (_) {}
    }
  });

  term.onExit(({ exitCode }) => {
    console.log(`[pty] exit ${exitCode} sesión=${sessionName}`);
    try { ws.close(); } catch (_) {}
  });

  ws.on("message", (raw) => {
    try {
      const msg = JSON.parse(raw.toString());
      if (msg.type === "resize" && term) {
        const cols = Math.max(20, Math.min(300, msg.cols || 80));
        const rows = Math.max(10, Math.min(100, msg.rows || 24));
        term.resize(cols, rows);
      } else if (msg.type === "input" && term) {
        term.write(msg.data || "");
      }
    } catch {
      if (term) term.write(raw.toString());
    }
  });

  ws.on("close", () => {
    // Solo desengancha el cliente; la sesión tmux SIGUE VIVA
    try { term.kill(); } catch (_) {}
    console.log(`[ws] cerrado — sesión "${sessionName}" sigue en el servidor`);
  });

  ws.on("error", (err) => console.error("[ws] error:", err.message));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[bebo] puerto ${PORT}`);
  ensureDefaultSession();
  if (AUTH_TOKEN) console.log("[bebo] auth token ACTIVO");
  else console.log("[bebo] sin token — modo abierto");
  startKeepAlive();
});

function startKeepAlive() {
  const host = process.env.RENDER_EXTERNAL_HOSTNAME;
  if (!host) {
    console.log("[keepalive] sin RENDER_EXTERNAL_HOSTNAME");
    return;
  }
  const url = `https://${host}/healthz`;
  const INTERVAL_MS = 10 * 60 * 1000;
  setInterval(() => {
    https
      .get(url, (res) => {
        res.resume();
        console.log(`[keepalive] ${url} -> ${res.statusCode}`);
      })
      .on("error", (err) => console.warn("[keepalive]", err.message));
  }, INTERVAL_MS);
  console.log(`[keepalive] cada 10 min → ${url}`);
}

process.on("SIGTERM", () => {
  console.log("[bebo] SIGTERM");
  server.close(() => process.exit(0));
});
