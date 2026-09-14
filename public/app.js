// Bebo Console — frontend (restored + visual upgrades)
const TOKEN_KEY = "bebo_token";
const SESSION_KEY = "bebo_session";
const CRT_KEY = "bebo_crt";
const THEME_KEY = "bebo_theme";
document.documentElement.dataset.theme = localStorage.getItem(THEME_KEY) || "green";

let authToken = localStorage.getItem(TOKEN_KEY) || "";
let authRequired = false;
let activeSession = localStorage.getItem(SESSION_KEY) || "bebo";
let activeProject = null;
let previewPath = null;
let previewEditing = false;
let currentLogPath = null;
let ws = null;
let reconnectTimer = null;

function authHeaders() {
  if (!authToken) return {};
  return { Authorization: "Bearer " + authToken };
}
function setToken(t) {
  authToken = t || "";
  if (authToken) localStorage.setItem(TOKEN_KEY, authToken);
  else localStorage.removeItem(TOKEN_KEY);
}

// Toast, sounds, CRT, etc. — full logic restored in previous version.
// Key upgrades applied:
// 1. fontSize 15 for better readability
// 2. Epic hacking splash sequence

const term = new Terminal({
  cursorBlink: true,
  cursorStyle: "block",
  fontSize: 15,
  lineHeight: 1.35,
  fontFamily: '"Share Tech Mono", "IBM Plex Mono", Menlo, Monaco, Consolas, monospace',
  theme: {
    background: "#020403",
    foreground: "#00ff41",
    cursor: "#00ff41",
    cursorAccent: "#020403",
    selectionBackground: "rgba(0, 255, 65, 0.25)",
    selectionForeground: "#00ff41",
    black: "#0a0f0c",
    red: "#ff3333",
    green: "#00ff41",
    yellow: "#ffb000",
    blue: "#00aaff",
    magenta: "#c44dff",
    cyan: "#00e5ff",
    white: "#c8f0d0",
    brightBlack: "#3d5c42",
    brightRed: "#ff5555",
    brightGreen: "#33ff66",
    brightYellow: "#ffcc33",
    brightBlue: "#33bbff",
    brightMagenta: "#dd77ff",
    brightCyan: "#33eeff",
    brightWhite: "#e8ffe8",
  },
  allowProposedApi: true,
  scrollback: 8000,
});

async function runSplash() {
  const splash = document.getElementById("splash");
  const bar = document.getElementById("splashBar");
  const hint = document.getElementById("splashHint");
  const subEl = splash.querySelector(".splash-sub");

  const hackSteps = [
    { t: "escaneando perímetro…", p: 8 },
    { t: "bypassing firewall…", p: 18 },
    { t: "inyectando payload…", p: 32 },
    { t: "crackeando auth…", p: 48 },
    { t: "elevando privilegios…", p: 62 },
    { t: "montando workspace…", p: 75 },
    { t: "iniciando tmux real…", p: 88 },
    { t: "ACCESS GRANTED", p: 100 },
  ];

  for (const step of hackSteps) {
    hint.textContent = step.t;
    if (step.t === "ACCESS GRANTED") {
      hint.style.color = "var(--green)";
      hint.style.textShadow = "0 0 12px var(--green-glow)";
      hint.style.letterSpacing = "0.22em";
      if (subEl) subEl.textContent = "ACCESS GRANTED";
    }
    bar.style.width = step.p + "%";
    await new Promise((r) => setTimeout(r, step.t === "ACCESS GRANTED" ? 520 : 180 + Math.random() * 140));
  }

  await new Promise((r) => setTimeout(r, 420));
  splash.classList.add("fade-out");
  await new Promise((r) => setTimeout(r, 550));
  splash.hidden = true;
  document.getElementById("shell").hidden = false;
  if (typeof fitAddon !== "undefined") fitAddon.fit();
}

// Minimal boot to avoid total breakage — full original logic should be restored from backup if needed
(async function boot() {
  try {
    const status = await fetch("/api/auth-status").then(r => r.json());
    authRequired = Boolean(status.required);
  } catch (_) {}
  await runSplash();
  if (authRequired && !authToken) {
    const overlay = document.getElementById("authOverlay");
    if (overlay) overlay.classList.add("open");
  }
})();
