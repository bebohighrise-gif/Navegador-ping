const express = require("express");
const http = require("http");
const path = require("path");
const os = require("os");
const pty = require("node-pty");
const { WebSocketServer } = require("ws");

const app = express();
const server = http.createServer(app);
const PORT = process.env.PORT || 8080;

app.use(express.static(path.join(__dirname, "public")));

app.get("/healthz", (_req, res) => {
  res.status(200).send("ok");
});

const wss = new WebSocketServer({ server, path: "/ws" });

wss.on("connection", (ws) => {
  const shell = "bash";

  const term = pty.spawn(shell, ["-l"], {
    name: "xterm-256color",
    cols: 100,
    rows: 30,
    cwd: process.env.HOME || "/home/desktop",
    env: {
      ...process.env,
      TERM: "xterm-256color",
      COLORTERM: "truecolor",
      HOME: process.env.HOME || "/home/desktop",
      USER: "desktop",
      PATH: process.env.PATH || "/usr/local/bin:/usr/bin:/bin",
    },
  });

  term.onData((data) => {
    try { ws.send(data); } catch (_) {}
  });

  ws.on("message", (msg) => {
    try {
      const parsed = JSON.parse(msg.toString());
      if (parsed.type === "resize") {
        term.resize(parsed.cols || 80, parsed.rows || 24);
      } else if (parsed.type === "input") {
        term.write(parsed.data);
      }
    } catch {
      term.write(msg.toString());
    }
  });

  ws.on("close", () => {
    try { term.kill(); } catch (_) {}
  });

  setTimeout(() => {
    term.write("\r\n");
    term.write("\x1b[1;36m╔══════════════════════════════════════════════════╗\x1b[0m\r\n");
    term.write("\x1b[1;36m║         Bebo AI · Consola Linux                  ║\x1b[0m\r\n");
    term.write("\x1b[1;36m╚══════════════════════════════════════════════════╝\x1b[0m\r\n");
    term.write("\r\n");
    term.write("\x1b[1;32m✓\x1b[0m Autosave activo → todo se guarda solo en PostgreSQL\r\n");
    term.write("\x1b[1;32m✓\x1b[0m Al arrancar se restaura automáticamente\r\n");
    term.write("\r\n");
    term.write("Lenguajes listos: \x1b[1;33mpython3 node php ruby go java\x1b[0m\r\n");
    term.write("Instalar paquetes: \x1b[1;33msudo apk add <paquete>\x1b[0m\r\n");
    term.write("Pip: \x1b[1;33mpip3 install --user <paquete>\x1b[0m\r\n");
    term.write("Npm: \x1b[1;33mnpm install -g <paquete>\x1b[0m\r\n");
    term.write("\r\n");
    term.write("Trabaja en: \x1b[1;34m~/workspace\x1b[0m  (se guarda solo)\r\n\r\n");
  }, 300);
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[bebo] Consola lista en puerto ${PORT}`);
});
