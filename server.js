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
  const shell = os.platform() === "win32" ? "powershell.exe" : "bash";

  const term = pty.spawn(shell, [], {
    name: "xterm-256color",
    cols: 100,
    rows: 30,
    cwd: process.env.HOME || "/home/desktop",
    env: {
      ...process.env,
      TERM: "xterm-256color",
      COLORTERM: "truecolor",
      PS1: "\\[\\e[1;32m\\]desktop@beboai\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]$ ",
    },
  });

  term.onData((data) => {
    try {
      ws.send(data);
    } catch (_) {}
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
    try {
      term.kill();
    } catch (_) {}
  });

  // Banner de bienvenida
  setTimeout(() => {
    term.write("\r\n");
    term.write("\x1b[1;36m╔══════════════════════════════════════════════╗\x1b[0m\r\n");
    term.write("\x1b[1;36m║         Bebo AI · Consola Linux              ║\x1b[0m\r\n");
    term.write("\x1b[1;36m╚══════════════════════════════════════════════╝\x1b[0m\r\n");
    term.write("\r\n");
    term.write("Escribe \x1b[1;33mdb\x1b[0m para ver comandos de persistencia.\r\n");
    term.write("Todo lo importante guárdalo con: \x1b[1;32mdb save archivo\x1b[0m\r\n\r\n");
  }, 200);
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Consola lista en puerto ${PORT}`);
});
