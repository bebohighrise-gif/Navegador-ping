const express = require("express");
const http = require("http");
const path = require("path");
const pty = require("node-pty");
const { WebSocketServer } = require("ws");

const app = express();
const server = http.createServer(app);
const PORT = Number(process.env.PORT) || 8080;
const HOME = process.env.HOME || "/home/desktop";

app.use(express.static(path.join(__dirname, "public")));

app.get("/healthz", (_req, res) => {
  res.status(200).type("text").send("ok");
});

app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

const wss = new WebSocketServer({ server, path: "/ws" });

wss.on("connection", (ws, req) => {
  const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress || "?";
  console.log(`[ws] conexión desde ${ip}`);

  let term;
  try {
    term = pty.spawn("bash", ["-l"], {
      name: "xterm-256color",
      cols: 100,
      rows: 30,
      cwd: HOME,
      env: {
        ...process.env,
        TERM: "xterm-256color",
        COLORTERM: "truecolor",
        HOME,
        USER: "desktop",
        SHELL: "/bin/bash",
        LANG: "C.UTF-8",
        PATH: process.env.PATH || "/home/desktop/.local/bin:/usr/local/bin:/usr/bin:/bin",
      },
    });
  } catch (err) {
    console.error("[pty] error:", err);
    ws.close();
    return;
  }

  term.onData((data) => {
    if (ws.readyState === ws.OPEN) {
      try { ws.send(data); } catch (_) {}
    }
  });

  term.onExit(({ exitCode }) => {
    console.log(`[pty] shell exit ${exitCode}`);
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
    try { term.kill(); } catch (_) {}
    console.log("[ws] cerrado");
  });

  ws.on("error", (err) => {
    console.error("[ws] error:", err.message);
  });

  // Banner
  setTimeout(() => {
    if (!term) return;
    const lines = [
      "",
      "\x1b[1;36m╔══════════════════════════════════════════════════╗\x1b[0m",
      "\x1b[1;36m║           Bebo AI · Consola Linux                ║\x1b[0m",
      "\x1b[1;36m╚══════════════════════════════════════════════════╝\x1b[0m",
      "",
      "\x1b[1;32m✓\x1b[0m Autosave ON — ~/workspace se guarda solo en PostgreSQL",
      "\x1b[1;32m✓\x1b[0m Al arrancar se restaura automáticamente",
      "",
      "Lenguajes: \x1b[1;33mpython3  node  php  ruby  go  java\x1b[0m",
      "Sistema:   \x1b[1;33msudo apk add <paquete>\x1b[0m",
      "Python:    \x1b[1;33mpip3 install --user <pkg>\x1b[0m",
      "Node:      \x1b[1;33mnpm install -g <pkg>\x1b[0m",
      "",
      "Directorio de trabajo: \x1b[1;34m~/workspace\x1b[0m",
      "",
    ];
    for (const line of lines) {
      term.write(line + "\r\n");
    }
  }, 250);
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[bebo] Consola lista · puerto ${PORT}`);
});

process.on("SIGTERM", () => {
  console.log("[bebo] SIGTERM — cerrando");
  server.close(() => process.exit(0));
});
