// ==================================================================
// Bebo Console — frontend
// ==================================================================
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

// ------------------------------------------------------------------
// Toast
// ------------------------------------------------------------------
const toastHost = document.getElementById("toastHost");
function showToast(message, { type = "error", duration = 8000 } = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (type === "success" ? " success" : "");
  const msg = document.createElement("div");
  msg.className = "toast-msg";
  msg.textContent = message;
  const actions = document.createElement("div");
  actions.className = "toast-actions";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.textContent = "Copiar";
  copyBtn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(message);
      copyBtn.textContent = "Copiado";
      copyBtn.classList.add("copy-done");
      setTimeout(() => { copyBtn.textContent = "Copiar"; copyBtn.classList.remove("copy-done"); }, 1500);
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = message;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      copyBtn.textContent = "Copiado";
    }
  };
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.textContent = "Cerrar";
  closeBtn.onclick = () => el.remove();
  actions.append(copyBtn, closeBtn);
  el.append(msg, actions);
  toastHost.appendChild(el);
  if (type === "success" && typeof sfxOk === "function") sfxOk();
  if (duration > 0) setTimeout(() => el.parentNode && el.remove(), duration);
}
function showError(err) {
  const text = (err && err.data && err.data.error) || (err && err.message) || String(err) || "Error";
  sfxError();
  showToast(text, { type: "error" });
}

// ------------------------------------------------------------------
// Sonidos hacker (Web Audio — sin archivos externos)
// ------------------------------------------------------------------
let audioCtx = null;
let sfxMuted = localStorage.getItem("bebo_sfx_mute") === "1";
function getAudio() {
  if (sfxMuted) return null;
  if (!audioCtx) {
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (_) { return null; }
  }
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

function tone(freq, dur, type = "square", gain = 0.08, slideTo = null) {
  const ctx = getAudio();
  if (!ctx) return;
  const t0 = ctx.currentTime;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (slideTo != null) osc.frequency.exponentialRampToValueAtTime(Math.max(1, slideTo), t0 + dur);
  g.gain.setValueAtTime(gain, t0);
  g.gain.exponentialRampToValueAtTime(0.001, t0 + dur);
  osc.connect(g);
  g.connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

function noiseBurst(dur = 0.06, gain = 0.05) {
  const ctx = getAudio();
  if (!ctx) return;
  const len = Math.floor(ctx.sampleRate * dur);
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const g = ctx.createGain();
  g.gain.value = gain;
  src.connect(g);
  g.connect(ctx.destination);
  src.start();
}

/** Error: beep descendente + ruido */
function sfxError() {
  tone(440, 0.08, "square", 0.1, 120);
  setTimeout(() => noiseBurst(0.08, 0.06), 40);
  setTimeout(() => tone(180, 0.12, "sawtooth", 0.07, 80), 90);
}

/** Éxito corto */
function sfxOk() {
  tone(520, 0.05, "square", 0.06);
  setTimeout(() => tone(780, 0.07, "square", 0.05), 50);
}

/** Tick de instalación / actividad rápida */
function sfxTick() {
  tone(900 + Math.random() * 400, 0.018, "square", 0.035);
}

/** Ráfaga de instalación (estilo paquetes bajando) */
let installTimer = null;
function sfxInstallStart() {
  if (installTimer) return;
  let n = 0;
  installTimer = setInterval(() => {
    sfxTick();
    n++;
    if (n > 40) sfxInstallStop();
  }, 45);
}
function sfxInstallStop() {
  if (installTimer) { clearInterval(installTimer); installTimer = null; }
  tone(600, 0.04, "square", 0.04);
}

/** Boot splash blips */
function sfxBoot() {
  tone(200, 0.05, "square", 0.05);
  setTimeout(() => tone(400, 0.04, "square", 0.04), 80);
  setTimeout(() => tone(600, 0.05, "square", 0.04), 150);
}

// Unlock audio on first user gesture
["click", "keydown", "touchstart"].forEach((ev) => {
  window.addEventListener(ev, () => getAudio(), { once: true, passive: true });
});



// ------------------------------------------------------------------
// CRT settings
// ------------------------------------------------------------------
function loadCrt() {
  try {
    return JSON.parse(localStorage.getItem(CRT_KEY) || "{}");
  } catch (_) {
    return {};
  }
}
function applyCrt(cfg) {
  const scan = cfg.scanlines ?? 35;
  const glow = cfg.glow ?? 40;
  const vig = cfg.vignette ?? 30;
  document.documentElement.style.setProperty("--crt-scanlines", String(scan / 100));
  document.documentElement.style.setProperty("--crt-glow", String(glow));
  document.documentElement.style.setProperty("--crt-vignette", String(vig));
  document.body.classList.toggle("crt-flicker", Boolean(cfg.flicker));
  const s = document.getElementById("crtScanlines");
  const g = document.getElementById("crtGlow");
  const v = document.getElementById("crtVignette");
  const f = document.getElementById("crtFlicker");
  const m = document.getElementById("sfxMute");
  if (s) s.value = scan;
  if (g) g.value = glow;
  if (v) v.value = vig;
  if (f) f.checked = Boolean(cfg.flicker);
  if (m) m.checked = sfxMuted;
}
function saveCrt() {
  const cfg = {
    scanlines: Number(document.getElementById("crtScanlines").value),
    glow: Number(document.getElementById("crtGlow").value),
    vignette: Number(document.getElementById("crtVignette").value),
    flicker: document.getElementById("crtFlicker").checked,
  };
  localStorage.setItem(CRT_KEY, JSON.stringify(cfg));
  applyCrt(cfg);
}
applyCrt(loadCrt());
["crtScanlines", "crtGlow", "crtVignette", "crtFlicker"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", saveCrt);
});
const sfxMuteEl = document.getElementById("sfxMute");
if (sfxMuteEl) {
  sfxMuteEl.checked = sfxMuted;
  sfxMuteEl.addEventListener("change", () => {
    sfxMuted = sfxMuteEl.checked;
    localStorage.setItem("bebo_sfx_mute", sfxMuted ? "1" : "0");
    if (!sfxMuted) sfxOk();
  });
}

document.getElementById("settingsBtn").onclick = () => {
  document.getElementById("settingsOverlay").classList.add("open");
};
document.getElementById("settingsClose").onclick = () => {
  document.getElementById("settingsOverlay").classList.remove("open");
};
document.getElementById("settingsReset").onclick = () => {
  localStorage.removeItem(CRT_KEY);
  applyCrt({});
};
const themeSelect = document.getElementById("themeSelect");
if (themeSelect) {
  themeSelect.value = document.documentElement.dataset.theme;
  themeSelect.addEventListener("change", () => {
    document.documentElement.dataset.theme = themeSelect.value;
    localStorage.setItem(THEME_KEY, themeSelect.value);
  });
}
document.getElementById("settingsOverlay").addEventListener("click", (e) => {
  if (e.target.id === "settingsOverlay") e.target.classList.remove("open");
});

// ------------------------------------------------------------------
// Splash — máscara hacker + BEBO HACKING
// ------------------------------------------------------------------
async function runSplash() {
  sfxBoot();
  const splash = document.getElementById("splash");
  const maskEl = document.getElementById("splashMask");
  const bar = document.getElementById("splashBar");
  const hint = document.getElementById("splashHint");
  const steps = [
    "cargando módulos…",
    "iniciando tmux…",
    "montando workspace…",
    "estableciendo enlace…",
    "acceso concedido",
  ];
  for (let i = 0; i < steps.length; i++) {
    hint.textContent = steps[i];
    bar.style.width = ((i + 1) / steps.length) * 100 + "%";
    sfxTick();
    await new Promise((r) => setTimeout(r, 220 + Math.random() * 100));
  }
  sfxOk();
  await new Promise((r) => setTimeout(r, 350));
  splash.classList.add("fade-out");
  await new Promise((r) => setTimeout(r, 500));
  splash.hidden = true;
  document.getElementById("shell").hidden = false;
  fitAddon.fit();
  sendResize();
}

// ------------------------------------------------------------------
// Terminal
// ------------------------------------------------------------------
const term = new Terminal({
  cursorBlink: true,
  cursorStyle: "block",
  fontSize: 14,
  lineHeight: 1.3,
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
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.loadAddon(new WebLinksAddon.WebLinksAddon());
term.open(document.getElementById("terminal"));
fitAddon.fit();

const connBadge = document.getElementById("conn");
const connText = document.getElementById("connText");
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");

function setStatus(online, text) {
  connBadge.className = "conn-badge " + (online ? "online" : "offline");
  connText.textContent = online ? "en línea" : "desconectado";
  statusDot.className = online ? "ok" : "err";
  statusText.textContent = text;
}
function sendResize() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
  }
}
function sendInput(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "input", data }));
  }
}

function connect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (authRequired && !authToken) {
    setStatus(false, "Esperando token…");
    return;
  }
  if (ws) {
    try { ws.onclose = null; ws.close(); } catch (_) {}
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let url = proto + "://" + location.host + "/ws?session=" + encodeURIComponent(activeSession);
  if (authToken) url += "&token=" + encodeURIComponent(authToken);
  ws = new WebSocket(url);

  ws.onopen = () => {
    setStatus(true, "Shell activa");
    sendResize();
    term.focus();
  };
  ws.onmessage = (ev) => {
    const chunk = typeof ev.data === "string" ? ev.data : new TextDecoder().decode(ev.data);
    term.write(typeof ev.data === "string" ? ev.data : new Uint8Array(ev.data));
    // Sonidos de instalación / actividad
    const low = chunk.toLowerCase();
    if (/npm install|pip install|yarn |pnpm |apt[- ]get|apk add|go get|bundle install|composer |downloading|installing|resolving|fetching/.test(low)) {
      sfxInstallStart();
    }
    if (/added \d|successfully installed|done\.|complete|exit code|error:|err!/.test(low)) {
      sfxInstallStop();
    }
    if (/\berror\b|\bfailed\b|\bEADDRINUSE\b|\bENOENT\b/i.test(chunk) && chunk.length < 400) {
      // no spam: solo si parece línea de error corta
      if (Math.random() < 0.3) sfxError();
    }
  };
  ws.onclose = (ev) => {
    if (ev.code === 4001) {
      setStatus(false, "Token inválido");
      showToast("Token inválido — acceso denegado", { type: "error" });
      showAuthOverlay(true);
      return;
    }
    setStatus(false, "Reconectando en 2s…");
    reconnectTimer = setTimeout(connect, 2000);
  };
  ws.onerror = () => setStatus(false, "Error de conexión");
}

term.onData((d) => sendInput(d));
window.addEventListener("resize", () => { fitAddon.fit(); sendResize(); });
document.querySelector(".term-wrap").addEventListener("click", () => term.focus());

// ------------------------------------------------------------------
// Utils
// ------------------------------------------------------------------
function shellQuote(str) { return "'" + str.replace(/'/g, `'\\''`) + "'"; }
function formatBytes(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}
function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function fetchJSON(url, opts = {}) {
  const headers = { ...authHeaders(), ...(opts.headers || {}) };
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const res = await fetch(url, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { showAuthOverlay(true); throw Object.assign(new Error("unauthorized"), { data }); }
  if (!res.ok) throw Object.assign(new Error(data.error || "request_failed"), { data });
  return data;
}
function downloadUrl(url) {
  const a = document.createElement("a");
  a.href = url + (authToken ? (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(authToken) : "");
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const svgFolder = '<svg class="row-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.2 1.5H12.5A1.5 1.5 0 0 1 14 6v6a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12z"/></svg>';
const svgFile = '<svg class="row-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M4 2h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M9 2v3h3"/></svg>';
const svgChevron = '<svg class="chevron" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 1l4 4-4 4"/></svg>';

// ------------------------------------------------------------------
// Auth
// ------------------------------------------------------------------
const authOverlay = document.getElementById("authOverlay");
const tokenInput = document.getElementById("tokenInput");
function showAuthOverlay(force) {
  if (!authRequired && !force) return;
  authOverlay.classList.add("open");
  tokenInput.value = authToken || "";
  document.getElementById("authError").hidden = true;
  setTimeout(() => tokenInput.focus(), 50);
}
function hideAuthOverlay() { authOverlay.classList.remove("open"); }
document.getElementById("tokenSubmit").onclick = () => {
  const t = tokenInput.value.trim();
  if (!t) return;
  setToken(t);
  hideAuthOverlay();
  loadSessions();
};
tokenInput.addEventListener("keydown", (e) => { if (e.key === "Enter") document.getElementById("tokenSubmit").click(); });

// ------------------------------------------------------------------
// Modal
// ------------------------------------------------------------------
let modalResolver = null;
function openModal(title, placeholder, def = "") {
  return new Promise((resolve) => {
    document.getElementById("modalTitle").textContent = title;
    const input = document.getElementById("modalInput");
    input.placeholder = placeholder;
    input.value = def;
    document.getElementById("modalOverlay").classList.add("open");
    modalResolver = resolve;
    setTimeout(() => { input.focus(); input.select(); }, 40);
  });
}
function closeModal(v) {
  document.getElementById("modalOverlay").classList.remove("open");
  if (modalResolver) { modalResolver(v); modalResolver = null; }
}
document.getElementById("modalCancel").onclick = () => closeModal(null);
document.getElementById("modalConfirm").onclick = () => closeModal(document.getElementById("modalInput").value.trim());
document.getElementById("modalInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") closeModal(document.getElementById("modalInput").value.trim());
  if (e.key === "Escape") closeModal(null);
});

// ------------------------------------------------------------------
// Sessions (tmux real)
// ------------------------------------------------------------------
const sessionSelect = document.getElementById("sessionSelect");
const sessionHome = document.getElementById("sessionHome");
const sessionWorkspace = document.getElementById("sessionWorkspace");
const sessionGrid = document.getElementById("sessionGrid");
const backSessions = document.getElementById("backSessions");
const sidebarSessionName = document.getElementById("sidebarSessionName");
const terminalSessionName = document.getElementById("terminalSessionName");
let sessionOpen = false;

function renderSessionCard(name, meta = {}) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "session-card" + (name === activeSession ? " current" : "");
  card.dataset.session = name;
  card.innerHTML = `
    <span class="session-card-icon" aria-hidden="true"><span></span></span>
    <span class="session-card-main">
      <strong>${escapeHtml(name)}</strong>
      <small>${meta.attached ? "activa ahora" : "sesión tmux disponible"}</small>
    </span>
    <span class="session-card-arrow">→</span>
    <span class="session-delete-countdown" aria-live="polite"></span>`;
  let pressTimer = null;
  let countdownTimer = null;
  let longPressed = false;
  const countdown = card.querySelector(".session-delete-countdown");
  const stopPress = () => {
    if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  };
  const beginDelete = () => {
    if (name === "bebo") {
      showToast("La sesión bebo es la sesión principal y no se puede eliminar", { type: "error", duration: 2600 });
      return;
    }
    longPressed = true;
    card.classList.add("deleting");
    let seconds = 5;
    card.style.setProperty("--delete-progress", "0%");
    countdown.textContent = String(seconds);
    countdownTimer = setInterval(() => {
      seconds -= 1;
      countdown.textContent = String(seconds);
      card.style.setProperty("--delete-progress", `${((5 - seconds) / 5) * 100}%`);
      if (seconds <= 0) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        deleteSession(name, card);
      }
    }, 1000);
  };
  card.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    pressTimer = setTimeout(beginDelete, 100);
  });
  card.addEventListener("pointerup", () => {
    if (longPressed) return;
    stopPress();
  });
  card.addEventListener("pointerleave", () => {
    if (!longPressed) stopPress();
  });
  card.addEventListener("pointercancel", () => {
    if (!longPressed) stopPress();
  });
  card.addEventListener("click", (event) => {
    if (longPressed) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    enterSession(name);
  }, true);
  return card;
}

async function deleteSession(name, card) {
  card.classList.add("deleting-final");
  try {
    await fetchJSON("/api/sessions/kill", { method: "POST", body: JSON.stringify({ name }) });
    card.classList.add("removed");
    showToast(`Sesión "${name}" eliminada`, { type: "success", duration: 3000 });
    setTimeout(loadSessions, 260);
  } catch (err) {
    card.classList.remove("deleting", "deleting-final");
    card.querySelector(".session-delete-countdown").textContent = "";
    showError(err);
  }
}

function enterSession(name) {
  activeSession = name;
  localStorage.setItem(SESSION_KEY, activeSession);
  sessionOpen = true;
  document.getElementById("shell").classList.add("in-session");
  sessionHome.hidden = true;
  sessionWorkspace.hidden = false;
  backSessions.hidden = false;
  sessionSelect.value = activeSession;
  sidebarSessionName.textContent = "sesión: " + activeSession;
  terminalSessionName.textContent = "sesión: " + activeSession;
  loadSystemStats();
  term.clear();
  connect();
  loadProjects();
  loadPorts();
  setTimeout(() => fitAddon.fit(), 30);
}

function leaveSession() {
  sessionOpen = false;
  document.getElementById("shell").classList.remove("in-session");
  if (ws) { try { ws.onclose = null; ws.close(); } catch (_) {} }
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  sessionWorkspace.hidden = true;
  sessionHome.hidden = false;
  backSessions.hidden = true;
  sidebarSessionName.textContent = "sesión: —";
  terminalSessionName.textContent = "sesión: —";
  loadSessions();
}

async function loadSystemStats() {
  if (!sessionOpen) return;
  try {
    await fetchJSON("/api/system");
  } catch (_) {}
}
setInterval(loadSystemStats, 10000);

backSessions.addEventListener("click", leaveSession);

async function loadSessions() {
  try {
    const data = await fetchJSON("/api/sessions");
    sessionSelect.innerHTML = "";
    const sessions = data.sessions || [];
    const names = sessions.map((s) => s.name);
    [...new Set(names)].forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (name === activeSession) opt.selected = true;
      sessionSelect.appendChild(opt);
    });
    if (sessionGrid) {
      sessionGrid.innerHTML = "";
      const available = [...new Set(names)];
      available.forEach((name) => {
        const info = sessions.find((item) => item.name === name) || {};
        sessionGrid.appendChild(renderSessionCard(name, info));
      });
      if (!available.length) sessionGrid.innerHTML = '<div class="session-loading">No hay sesiones activas. Creá una nueva para comenzar.</div>';
    }
  } catch (err) {
    if (err.message !== "unauthorized") showError(err);
    if (sessionGrid) sessionGrid.innerHTML = '<div class="session-loading">No se pudieron cargar las sesiones.</div>';
  }
}

sessionSelect.addEventListener("change", () => {
  enterSession(sessionSelect.value);
});

async function createSession() {
  const name = await openModal("Nueva sesión tmux", "nombre-sesion");
  if (!name) return;
  try {
    await fetchJSON("/api/sessions", { method: "POST", body: JSON.stringify({ name }) });
    activeSession = name.toLowerCase().replace(/[^a-z0-9_-]/g, "-").slice(0, 32);
    localStorage.setItem(SESSION_KEY, activeSession);
    await loadSessions();
    enterSession(activeSession);
  } catch (err) {
    showError(err);
  }
}
document.getElementById("newSessionBtn").onclick = createSession;
document.getElementById("newSessionHome").onclick = createSession;

document.querySelectorAll(".sidebar-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".sidebar-tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".sidebar .panel").forEach((panel) => panel.classList.toggle("panel-active", panel.classList.contains(tab.dataset.panel)));
    closeDrawerOnMobile();
  });
});

document.getElementById("clearTerminalBtn")?.addEventListener("click", () => {
  term.clear();
  term.focus();
});
document.getElementById("copyTerminalBtn")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const selection = term.getSelection();
  if (!selection) {
    showToast("Seleccioná texto en la terminal para copiarlo", { type: "error", duration: 2200 });
    return;
  }
  try {
    await navigator.clipboard.writeText(selection);
    button.textContent = "copiado";
    setTimeout(() => { button.textContent = "copiar"; }, 1400);
  } catch (_) { showToast("No se pudo copiar la selección", { type: "error", duration: 2200 }); }
});

const commandOverlay = document.getElementById("commandOverlay");
const commandInput = document.getElementById("commandInput");
const commandList = document.getElementById("commandList");
const commands = [
  ["Nueva sesión", "Crear una sesión tmux", createSession],
  ["Limpiar terminal", "Borrar la salida visible", () => { term.clear(); term.focus(); }],
  ["Abrir proyectos", "Mostrar el panel de proyectos", () => document.querySelector('[data-panel="projects"]')?.click()],
  ["Abrir archivos", "Mostrar el explorador de archivos", () => document.querySelector('[data-panel="files"]')?.click()],
  ["Volver a sesiones", "Cerrar el workspace actual", leaveSession],
];
function renderCommands(query = "") {
  const q = query.toLowerCase();
  commandList.innerHTML = "";
  commands.filter(([name, hint]) => (name + hint).toLowerCase().includes(q)).forEach(([name, hint], index) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "command-item";
    item.innerHTML = `<strong>${escapeHtml(name)}</strong><small>${escapeHtml(hint)}</small><kbd>${index + 1}</kbd>`;
    item.onclick = () => { commandOverlay.hidden = true; commands.find((cmd) => cmd[0] === name)[2](); };
    commandList.appendChild(item);
  });
}
function openCommands() {
  commandOverlay.hidden = false;
  renderCommands();
  commandInput.value = "";
  setTimeout(() => commandInput.focus(), 20);
}
function closeCommands() { commandOverlay.hidden = true; }
commandInput?.addEventListener("input", () => renderCommands(commandInput.value));
commandInput?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeCommands();
  if (event.key === "Enter") commandList.querySelector(".command-item")?.click();
});
commandOverlay?.addEventListener("click", (event) => { if (event.target === commandOverlay) closeCommands(); });
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openCommands(); }
  if (event.key === "Escape" && !commandOverlay.hidden) closeCommands();
});
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));

// ------------------------------------------------------------------
// Context menu
// ------------------------------------------------------------------
const ctxMenu = document.getElementById("ctxMenu");
let ctxTarget = null;
function showCtxMenu(x, y, target) {
  ctxTarget = target;
  ctxMenu.hidden = false;
  ctxMenu.style.left = x + "px";
  ctxMenu.style.top = y + "px";
}
function hideCtxMenu() { ctxMenu.hidden = true; ctxTarget = null; }
document.addEventListener("click", hideCtxMenu);

ctxMenu.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn || !ctxTarget) return;
  const { path: relPath, name, type } = ctxTarget;
  const action = btn.dataset.action;
  hideCtxMenu();

  if (action === "rename") {
    const newName = await openModal("Renombrar", "Nuevo nombre", name);
    if (!newName || newName === name) return;
    const parent = relPath.includes("/") ? relPath.slice(0, relPath.lastIndexOf("/")) : "";
    const to = parent ? parent + "/" + newName : newName;
    try {
      await fetchJSON("/api/rename", { method: "POST", body: JSON.stringify({ from: relPath, to }) });
      showToast("Renombrado: " + name + " → " + newName, { type: "success", duration: 3000 });
      if (activeProject) loadTree(activeProject, filesTree, 0);
      loadProjects();
    } catch (err) { showError(err); }
  } else if (action === "download") {
    if (type === "dir") {
      downloadUrl("/api/download-zip?path=" + encodeURIComponent(relPath));
    } else {
      downloadUrl("/api/download?path=" + encodeURIComponent(relPath));
    }
  } else if (action === "delete") {
    if (!confirm(type === "dir" ? `¿Eliminar carpeta "${name}" y todo su contenido? (también se registra en DB)` : `¿Eliminar "${name}"?`)) return;
    try {
      await fetchJSON("/api/delete", { method: "POST", body: JSON.stringify({ path: relPath }) });
      showToast("Eliminado (+ DB): " + name, { type: "success", duration: 3000 });
      if (previewPath === relPath) closePreview();
      if (type === "dir" && activeProject === relPath) {
        activeProject = null;
        filesTitle.textContent = "Archivos";
        filesTree.innerHTML = '<div class="tree-empty">Elegí un proyecto arriba.</div>';
        [newFileBtn, newFolderBtn, uploadBtn, downloadZipBtn].forEach((b) => (b.disabled = true));
      }
      if (activeProject) loadTree(activeProject, filesTree, 0);
      loadProjects();
      loadLogs();
    } catch (err) { showError(err); }
  }
});

// ------------------------------------------------------------------
// Projects / files
// ------------------------------------------------------------------
const projectsList = document.getElementById("projectsList");
const filesTree = document.getElementById("filesTree");
const filesTitle = document.getElementById("filesTitle");
const newFileBtn = document.getElementById("newFile");
const newFolderBtn = document.getElementById("newFolder");
const uploadBtn = document.getElementById("uploadBtn");
const downloadZipBtn = document.getElementById("downloadZipBtn");

async function loadProjects() {
  projectsList.innerHTML = '<div class="tree-empty">Cargando…</div>';
  try {
    const data = await fetchJSON("/api/projects");
    if (!data.projects || !data.projects.length) {
      projectsList.innerHTML = '<div class="tree-empty">Sin proyectos. Creá uno con +.</div>';
      return;
    }
    projectsList.innerHTML = "";
    data.projects.forEach((p) => {
      const row = document.createElement("button");
      row.className = "project-row" + (p.name === activeProject ? " active" : "");
      row.innerHTML = `${svgFolder}<span class="project-name">${escapeHtml(p.name)}</span><span class="project-meta">${p.itemCount}</span>`;
      let pressTimer = null;
      let projectDeleteTimer = null;
      let projectDeleteSeconds = 5;
      let longPressed = false;
      const projectTarget = { path: p.name, name: p.name, type: "dir" };
      row.onclick = () => selectProject(p.name);
      row.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        longPressed = false;
        pressTimer = setTimeout(() => {
          longPressed = true;
          projectDeleteSeconds = 5;
          row.classList.add("project-deleting");
          row.style.setProperty("--delete-progress", "0%");
          row.insertAdjacentHTML("beforeend", '<span class="project-delete-countdown">5</span>');
          const counter = row.querySelector(".project-delete-countdown");
          projectDeleteTimer = setInterval(async () => {
            projectDeleteSeconds -= 1;
            counter.textContent = String(projectDeleteSeconds);
            row.style.setProperty("--delete-progress", `${((5 - projectDeleteSeconds) / 5) * 100}%`);
            if (projectDeleteSeconds <= 0) {
              clearInterval(projectDeleteTimer);
              projectDeleteTimer = null;
              await deleteProject(p.name, row);
            }
          }, 1000);
        }, 100);
      });
      ["pointerup", "pointerleave", "pointercancel"].forEach((eventName) => row.addEventListener(eventName, () => {
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      }));
      row.addEventListener("click", (e) => {
        if (longPressed) { e.preventDefault(); e.stopImmediatePropagation(); longPressed = false; }
      }, true);
      row.oncontextmenu = (e) => {
        e.preventDefault();
        showCtxMenu(e.clientX, e.clientY, projectTarget);
      };
      projectsList.appendChild(row);
    });
  } catch (err) {
    if (err.message !== "unauthorized") {
      projectsList.innerHTML = '<div class="tree-empty">Error al cargar.</div>';
      showError(err);
    }
  }
}

function selectProject(name) {
  activeProject = name;
  filesTitle.textContent = "Archivos · " + name;
  [newFileBtn, newFolderBtn, uploadBtn, downloadZipBtn].forEach((b) => (b.disabled = false));
  loadProjects();
  loadTree(name, filesTree, 0);
  loadLogs();
  loadGitStatus(name);
  sendInput(`cd ~/workspace/${name} && clear\n`);
  closeDrawerOnMobile();
}

async function deleteProject(name, row) {
  try {
    await fetchJSON("/api/delete", { method: "POST", body: JSON.stringify({ path: name }) });
    if (activeProject === name) {
      activeProject = null;
      filesTitle.textContent = "Archivos";
      filesTree.innerHTML = '<div class="tree-empty">Elegí un proyecto arriba.</div>';
      [newFileBtn, newFolderBtn, uploadBtn, downloadZipBtn].forEach((b) => (b.disabled = true));
    }
    row.classList.add("project-removed");
    showToast(`Proyecto "${name}" eliminado`, { type: "success", duration: 3000 });
    setTimeout(loadProjects, 260);
  } catch (err) {
    row.classList.remove("project-deleting");
    row.querySelector(".project-delete-countdown")?.remove();
    showError(err);
  }
}

async function loadGitStatus(name) {
  try {
    await fetchJSON("/api/git-status?project=" + encodeURIComponent(name));
  } catch (_) {}
}

async function loadTree(relPath, container, depth) {
  container.innerHTML = '<div class="tree-empty">Cargando…</div>';
  try {
    const data = await fetchJSON("/api/tree?path=" + encodeURIComponent(relPath));
    renderTree(data.entries, container, relPath, depth);
  } catch (err) {
    container.innerHTML = '<div class="tree-empty">Error.</div>';
    showError(err);
  }
}

function renderTree(entries, container, basePath, depth) {
  container.innerHTML = "";
  if (!entries || !entries.length) {
    container.innerHTML = '<div class="tree-empty">Vacío.</div>';
    return;
  }
  entries.forEach((entry) => {
    const fullPath = basePath ? basePath + "/" + entry.name : entry.name;
    const row = document.createElement("button");
    row.className = "tree-row";
    row.style.setProperty("--depth", depth);
    if (entry.type === "dir") {
      row.innerHTML = `${svgChevron}${svgFolder}<span class="row-name">${escapeHtml(entry.name)}</span>`;
      const childWrap = document.createElement("div");
      childWrap.className = "tree-children";
      childWrap.style.display = "none";
      let loaded = false;
      row.onclick = async () => {
        const open = row.classList.toggle("open");
        childWrap.style.display = open ? "flex" : "none";
        if (open && !loaded) { loaded = true; await loadTree(fullPath, childWrap, depth + 1); }
      };
      row.oncontextmenu = (e) => {
        e.preventDefault();
        showCtxMenu(e.clientX, e.clientY, { path: fullPath, name: entry.name, type: "dir" });
      };
      container.append(row, childWrap);
    } else {
      row.innerHTML = `<span style="width:10px"></span>${fileLanguageIcon(entry.name)}<span class="row-name">${escapeHtml(entry.name)}</span><span class="row-size">${formatBytes(entry.size)}</span>`;
      row.onclick = () => openPreview(fullPath, entry.name);
      row.oncontextmenu = (e) => {
        e.preventDefault();
        showCtxMenu(e.clientX, e.clientY, { path: fullPath, name: entry.name, type: "file" });
      };
      container.appendChild(row);
    }
  });
}

function fileLanguageIcon(name) {
  const lower = name.toLowerCase();
  let language = "file";
  if (lower.endsWith(".py")) language = "python";
  else if (lower.endsWith(".js") || lower.endsWith(".jsx")) language = "javascript";
  else if (lower.endsWith(".ts") || lower.endsWith(".tsx")) language = "typescript";
  else if (lower.endsWith(".html")) language = "html";
  else if (lower.endsWith(".css") || lower.endsWith(".scss")) language = "css";
  else if (lower.endsWith(".json")) language = "json";
  else if (lower.endsWith(".md")) language = "markdown";
  else if (lower.endsWith(".sh")) language = "shell";
  else if (lower.endsWith(".go")) language = "go";
  else if (lower.endsWith(".rs")) language = "rust";
  else if (lower.endsWith(".java")) language = "java";
  else if (lower.endsWith(".php")) language = "php";
  else if (lower.endsWith(".sql")) language = "sql";
  const icons = {
    python: '<path d="M31 7c-10 0-9 4-9 4v5h10v2H14c-9 0-9 8-9 8s0 8 9 8h5v-5s0-4 4-4h9c9 0 9-8 9-8v-2c0-8-1-8-1-8H31Zm-6 5a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z"/><path d="M33 41c10 0 9-4 9-4v-5H32v-2h18c9 0 9-8 9-8s0-8-9-8h-5v5s0 4-4 4h-9c-9 0-9 8-9 8v2c0 8 1 8 1 8h9Zm6-5a2 2 0 1 1 0 4 2 2 0 0 1 0-4Z"/>',
    javascript: '<path d="M8 8h48v48H8z"/><path d="M35 45c1 3 3 5 6 5 2 0 4-1 4-3 0-2-1-3-5-5-5-2-8-5-8-10 0-5 4-9 10-9 5 0 8 2 10 6l-5 3c-1-2-2-3-5-3-2 0-3 1-3 3s1 3 5 4c6 3 8 6 8 11 0 6-5 9-11 9-6 0-10-3-12-8l6-3Zm-18-21h7v17c0 6-3 9-9 9-1 0-3 0-4-1l1-6c1 1 2 1 3 1 2 0 2-1 2-3V24Z" fill="currentColor"/>',
    typescript: '<path d="M8 8h48v48H8z"/><path d="M15 25h24v6h-9v21h-7V31h-8v-6Zm27 0h14v6h-8c-2 0-3 1-3 2 0 2 2 2 6 4 4 2 6 4 6 8 0 5-4 8-10 8-5 0-9-2-11-6l5-3c1 2 3 3 6 3 2 0 3-1 3-2s-1-2-5-3c-6-2-8-5-8-9 0-5 4-8 9-8Z" fill="currentColor"/>',
    html: '<path d="M7 7h50l-5 45-20 6-20-6L7 7Z"/><path d="m16 18 1 11h17l-1 6H18l1 7 13 4 13-4 2-24H16Z" fill="currentColor"/><path d="M25 18h22l-1 6H25z" fill="#fff"/>',
    css: '<path d="M7 7h50l-5 45-20 6-20-6L7 7Z"/><path d="M17 18h31l-1 6H18l1 7h27l-2 17-13 4-13-4-1-8h7l1 5 6 2 6-2 1-8H19l-2-19Z" fill="currentColor"/>',
    json: '<path d="M24 14c-7 0-10 4-10 10v5c0 3-1 5-5 5 4 0 5 2 5 5v5c0 6 3 10 10 10h4v-6h-3c-3 0-4-2-4-5v-5c0-4-2-6-5-7 3-1 5-3 5-7v-4c0-3 1-5 4-5h3v-6h-4Zm16 0c7 0 10 4 10 10v5c0 3 1 5 5 5-4 0-5 2-5 5v5c0 6-3 10-10 10h-4v-6h3c3 0 4-2 4-5v-5c0-4 2-6 5-7-3-1-5-3-5-7v-4c0-3-1-5-4-5h-3v-6h4Z"/>',
    markdown: '<path d="M6 15h52v34H6z"/><path d="M13 42V23h6l7 9 7-9h6v19h-7V34l-6 7-6-7v8h-7Zm31 0V23h7v13h7l-10 10-10-10h6v6Z" fill="currentColor"/>',
    shell: '<path d="M8 10h48v44H8z"/><path d="m17 22 9 8-9 8" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M32 39h15" stroke="currentColor" stroke-width="5" stroke-linecap="round"/>',
    go: '<path d="M10 31c5-15 26-22 43-11l-5 5c-12-6-25-2-30 6 5 8 18 12 30 6l5 5c-17 11-38 4-43-11Z"/><path d="M25 28h27v6H25z" fill="currentColor"/>',
    rust: '<path d="M32 7 38 10l7-1 3 7 7 3-1 8 4 6-4 6 1 8-7 3-3 7-8-1-6 4-6-4-8 1-3-7-7-3 1-8-4-6 4-6-1-8 7-3 3-7 8 1 6-4Z"/><path d="M22 43V22h11c7 0 10 3 10 8 0 4-2 6-5 7l6 6h-8l-5-6h-2v6h-7Zm7-12h4c2 0 3-1 3-2s-1-2-3-2h-4v4Z" fill="currentColor"/>',
    java: '<path d="M19 48h27v4H19z"/><path d="M22 26c8 3 12 5 7 10-5 3 4 5 8 1-1 8-15 8-15 2 0-4 8-6 0-13Zm12-12c9 6-4 8 1 12 5 4 4 7-2 9 4-5-6-7-1-13 3-3 4-4 2-8Z" fill="currentColor"/><path d="M15 54h34" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    php: '<path d="M6 32c4-12 48-12 52 0-4 12-48 12-52 0Z"/><text x="15" y="37" font-size="13" font-weight="700" fill="currentColor">PHP</text>',
    sql: '<ellipse cx="32" cy="16" rx="20" ry="8"/><path d="M12 16v15c0 5 9 8 20 8s20-3 20-8V16" fill="none" stroke="currentColor" stroke-width="5"/><path d="M12 31v15c0 5 9 8 20 8s20-3 20-8V31" fill="none" stroke="currentColor" stroke-width="5"/>',
    file: '<path d="M16 6h20l12 12v40H16z"/><path d="M36 6v14h12" fill="none" stroke="currentColor" stroke-width="4"/>',
  };
  return `<svg class="file-language-icon file-icon-${language}" viewBox="0 0 64 64" aria-label="${language}" role="img">${icons[language]}</svg>`;
}

document.getElementById("refreshProjects").onclick = loadProjects;
document.getElementById("refreshFiles").onclick = () => activeProject && loadTree(activeProject, filesTree, 0);

document.getElementById("newProject").onclick = async () => {
  const name = await openModal("Nuevo proyecto", "nombre-proyecto");
  if (!name) return;
  if (!/^[a-zA-Z0-9._-]+$/.test(name)) {
    showToast("Nombre inválido", { type: "error" });
    return;
  }
  try {
    await fetchJSON("/api/create", { method: "POST", body: JSON.stringify({ path: name, type: "dir" }) });
    showToast("Proyecto: " + name, { type: "success", duration: 3000 });
    await loadProjects();
    selectProject(name);
  } catch (err) { showError(err); }
};

newFileBtn.onclick = async () => {
  if (!activeProject) return;
  const name = await openModal("Nuevo archivo", "archivo.txt");
  if (!name) return;
  try {
    await fetchJSON("/api/create", { method: "POST", body: JSON.stringify({ path: activeProject + "/" + name, type: "file", content: "" }) });
    showToast("Creado: " + name, { type: "success", duration: 2500 });
    loadTree(activeProject, filesTree, 0);
  } catch (err) { showError(err); }
};

newFolderBtn.onclick = async () => {
  if (!activeProject) return;
  const name = await openModal("Nueva carpeta", "carpeta");
  if (!name) return;
  try {
    await fetchJSON("/api/create", { method: "POST", body: JSON.stringify({ path: activeProject + "/" + name, type: "dir" }) });
    showToast("Carpeta: " + name, { type: "success", duration: 2500 });
    loadTree(activeProject, filesTree, 0);
  } catch (err) { showError(err); }
};

// Upload
const fileInput = document.getElementById("fileInput");
uploadBtn.onclick = () => { if (activeProject) fileInput.click(); };
fileInput.onchange = async () => {
  const files = Array.from(fileInput.files || []);
  fileInput.value = "";
  if (!activeProject || !files.length) return;
  for (const file of files) {
    try {
      const buf = await file.arrayBuffer();
      const b64 = btoa(String.fromCharCode(...new Uint8Array(buf).reduce((a, c) => { a.push(c); return a; }, [])));
      // chunk-safe base64
      let binary = "";
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      const content_b64 = btoa(binary);
      const rel = activeProject + "/" + file.name;
      const isZip = file.name.toLowerCase().endsWith(".zip");
      await fetchJSON("/api/upload-b64", {
        method: "POST",
        body: JSON.stringify({
          path: rel,
          content_b64,
          extract: isZip,
          dest: activeProject,
        }),
      });
      showToast((isZip ? "ZIP extraído: " : "Subido: ") + file.name, { type: "success", duration: 3000 });
    } catch (err) {
      showError(err);
    }
  }
  loadTree(activeProject, filesTree, 0);
  loadProjects();
};

downloadZipBtn.onclick = () => {
  if (!activeProject) return;
  downloadUrl("/api/download-zip?path=" + encodeURIComponent(activeProject));
};

// ------------------------------------------------------------------
// Preview + edit
// ------------------------------------------------------------------
const previewOverlay = document.getElementById("previewOverlay");
const previewPanel = document.getElementById("previewPanel");
const previewName = document.getElementById("previewName");
const previewBody = document.getElementById("previewBody");
const previewEdit = document.getElementById("previewEdit");
const previewSave = document.getElementById("previewSave");

const EXT_TO_LANG = {
  ".js": "javascript", ".jsx": "javascript", ".ts": "javascript", ".tsx": "javascript",
  ".py": "python", ".sh": "bash", ".bash": "bash", ".json": "json",
  ".css": "css", ".scss": "css", ".html": "markup", ".xml": "markup",
  ".go": "go", ".java": "java", ".rb": "ruby", ".php": "php",
  ".sql": "sql", ".yml": "yaml", ".yaml": "yaml", ".md": "markdown",
};

function highlightCode(content, ext) {
  const lang = EXT_TO_LANG[ext] || "none";
  if (lang === "none" || !window.Prism || !Prism.languages[lang]) {
    const pre = document.createElement("pre");
    pre.textContent = content;
    return pre;
  }
  const pre = document.createElement("pre");
  pre.className = "language-" + lang;
  const code = document.createElement("code");
  code.className = "language-" + lang;
  code.innerHTML = Prism.highlight(content, Prism.languages[lang], lang);
  pre.appendChild(code);
  return pre;
}

let lastPreviewContent = "";
let lastPreviewExt = "";

async function openPreview(fullPath, displayName) {
  previewPath = fullPath;
  previewEditing = false;
  previewName.textContent = displayName;
  previewEdit.hidden = false;
  previewSave.hidden = true;
  previewBody.innerHTML = '<div class="preview-note">Cargando…</div>';
  previewOverlay.classList.add("open");
  previewPanel.classList.add("open");
  try {
    const data = await fetchJSON("/api/file?path=" + encodeURIComponent(fullPath));
    lastPreviewContent = data.content;
    lastPreviewExt = data.ext || "";
    previewBody.innerHTML = "";
    previewBody.appendChild(highlightCode(data.content, lastPreviewExt));
  } catch (err) {
    const reason = err.data && err.data.error;
    const messages = {
      binary: "Archivo binario — usá Descargar.",
      too_large: "Demasiado grande para preview (máx 200 KB).",
      not_found: "No existe.",
    };
    previewBody.innerHTML = `<div class="preview-note error">${escapeHtml(messages[reason] || "Error al leer.")}</div>`;
    previewEdit.hidden = true;
    showError(err);
  }
}

function closePreview() {
  previewOverlay.classList.remove("open");
  previewPanel.classList.remove("open");
  previewPath = null;
  previewEditing = false;
}
document.getElementById("previewClose").onclick = closePreview;
previewOverlay.onclick = closePreview;

previewEdit.onclick = () => {
  if (!previewPath) return;
  previewEditing = true;
  previewEdit.hidden = true;
  previewSave.hidden = false;
  const ta = document.createElement("textarea");
  ta.className = "preview-editor";
  ta.value = lastPreviewContent;
  previewBody.innerHTML = "";
  previewBody.appendChild(ta);
  ta.focus();
};

previewSave.onclick = async () => {
  if (!previewPath || !previewEditing) return;
  const ta = previewBody.querySelector("textarea");
  if (!ta) return;
  try {
    await fetchJSON("/api/write", {
      method: "POST",
      body: JSON.stringify({ path: previewPath, content: ta.value }),
    });
    lastPreviewContent = ta.value;
    previewEditing = false;
    previewEdit.hidden = false;
    previewSave.hidden = true;
    previewBody.innerHTML = "";
    previewBody.appendChild(highlightCode(lastPreviewContent, lastPreviewExt));
    showToast("Guardado", { type: "success", duration: 2000 });
  } catch (err) { showError(err); }
};

document.getElementById("previewCat").onclick = () => {
  if (!previewPath) return;
  const rel = activeProject && previewPath.startsWith(activeProject + "/")
    ? previewPath.slice(activeProject.length + 1)
    : previewPath;
  sendInput(`cat -- ${shellQuote(rel)}\n`);
  closePreview();
  term.focus();
};

document.getElementById("previewDownload").onclick = () => {
  if (!previewPath) return;
  downloadUrl("/api/download?path=" + encodeURIComponent(previewPath));
};

document.getElementById("previewDelete").onclick = async () => {
  if (!previewPath) return;
  const name = previewPath.split("/").pop();
  if (!confirm(`¿Eliminar "${name}"? (también se registra en DB)`)) return;
  try {
    await fetchJSON("/api/delete", { method: "POST", body: JSON.stringify({ path: previewPath }) });
    showToast("Eliminado (+ DB): " + name, { type: "success", duration: 3000 });
    closePreview();
    if (activeProject) loadTree(activeProject, filesTree, 0);
    loadProjects();
  } catch (err) { showError(err); }
};

// ------------------------------------------------------------------
// Logs
// ------------------------------------------------------------------
const logsList = document.getElementById("logsList");
const logOverlay = document.getElementById("logOverlay");
const logPanel = document.getElementById("logPanel");
const logBody = document.getElementById("logBody");
const logName = document.getElementById("logName");

async function loadLogs() {
  if (!activeProject) {
    logsList.innerHTML = '<div class="tree-empty">Seleccioná un proyecto.</div>';
    return;
  }
  logsList.innerHTML = '<div class="tree-empty">Buscando logs…</div>';
  try {
    const data = await fetchJSON("/api/logs?project=" + encodeURIComponent(activeProject));
    // También opción de captura tmux de la sesión
    logsList.innerHTML = "";
    const sessBtn = document.createElement("button");
    sessBtn.className = "log-row";
    sessBtn.innerHTML = `<span>📺 sesión tmux (${escapeHtml(activeSession)})</span>`;
    sessBtn.onclick = () => openSessionLog();
    logsList.appendChild(sessBtn);

    if (!data.logs || !data.logs.length) {
      const empty = document.createElement("div");
      empty.className = "tree-empty";
      empty.textContent = "Sin archivos .log en el proyecto.";
      logsList.appendChild(empty);
      return;
    }
    data.logs.forEach((log) => {
      const row = document.createElement("button");
      row.className = "log-row";
      row.innerHTML = `<span>${escapeHtml(log.name)}</span><span class="row-size">${formatBytes(log.size)}</span>`;
      row.onclick = () => openLog(log.path, log.name);
      logsList.appendChild(row);
    });
  } catch (err) {
    logsList.innerHTML = '<div class="tree-empty">Error logs.</div>';
  }
}

async function openLog(path, name) {
  currentLogPath = path;
  logName.textContent = name;
  logBody.textContent = "Cargando…";
  logOverlay.classList.add("open");
  logPanel.classList.add("open");
  try {
    const data = await fetchJSON("/api/log-tail?path=" + encodeURIComponent(path));
    logBody.textContent = data.content || "(vacío)";
  } catch (err) {
    logBody.textContent = "Error: " + ((err.data && err.data.error) || err.message);
    showError(err);
  }
}

async function openSessionLog() {
  currentLogPath = null;
  logName.textContent = "tmux · " + activeSession;
  logBody.textContent = "Cargando…";
  logOverlay.classList.add("open");
  logPanel.classList.add("open");
  try {
    const data = await fetchJSON("/api/session-log?session=" + encodeURIComponent(activeSession));
    logBody.textContent = data.content || "(vacío)";
  } catch (err) {
    logBody.textContent = "Error: " + ((err.data && err.data.error) || err.message);
  }
}

function closeLog() {
  logOverlay.classList.remove("open");
  logPanel.classList.remove("open");
}
document.getElementById("logClose").onclick = closeLog;
logOverlay.onclick = closeLog;
document.getElementById("refreshLogs").onclick = loadLogs;
document.getElementById("logRefresh").onclick = () => {
  if (currentLogPath) openLog(currentLogPath, logName.textContent);
  else openSessionLog();
};
document.getElementById("logCopy").onclick = async () => {
  try {
    await navigator.clipboard.writeText(logBody.textContent);
    showToast("Log copiado", { type: "success", duration: 2000 });
  } catch (_) {
    showToast("No se pudo copiar", { type: "error" });
  }
};

// ------------------------------------------------------------------
// Drawer mobile
// ------------------------------------------------------------------
const sidebar = document.getElementById("sidebar");
const sidebarBackdrop = document.getElementById("sidebarBackdrop");
function closeDrawer() {
  sidebar.classList.remove("open");
  sidebarBackdrop.classList.remove("open");
}
function closeDrawerOnMobile() {
  if (window.matchMedia("(max-width: 760px)").matches) closeDrawer();
}
document.getElementById("drawerToggle").onclick = () => {
  sidebar.classList.contains("open")
    ? closeDrawer()
    : (sidebar.classList.add("open"), sidebarBackdrop.classList.add("open"));
};
sidebarBackdrop.onclick = closeDrawer;


// ------------------------------------------------------------------
// tmux multiplexación avanzada (comandos reales al cliente pty)
// ------------------------------------------------------------------
function tmuxCmd(keys) {
  // keys: string sent as if typed (prefix is Ctrl-b by default)
  sendInput(keys);
}
document.getElementById("tmuxSplitV")?.addEventListener("click", () => {
  // Ctrl-b %
  sendInput("\x02%");
  showToast("split vertical", { type: "success", duration: 1500 });
});
document.getElementById("tmuxSplitH")?.addEventListener("click", () => {
  // Ctrl-b "
  sendInput('\x02"');
  showToast("split horizontal", { type: "success", duration: 1500 });
});
document.getElementById("tmuxNewWin")?.addEventListener("click", () => {
  // Ctrl-b c
  sendInput("\x02c");
  showToast("nueva ventana tmux", { type: "success", duration: 1500 });
});


// ------------------------------------------------------------------
// Puertos web (proxy /p/PORT sin dominio de pago)
// ------------------------------------------------------------------
const portsList = document.getElementById("portsList");

async function loadPorts() {
  if (!portsList) return;
  portsList.innerHTML = '<div class="tree-empty">Escaneando…</div>';
  try {
    const data = await fetchJSON("/api/ports");
    const ports = data.ports || [];
    if (!ports.length) {
      portsList.innerHTML = '<div class="tree-empty">Ningún puerto en escucha.<br>Ej: <code>python3 -m http.server 8000</code><br>luego abrí /p/8000/</div>';
      return;
    }
    portsList.innerHTML = "";
    ports.forEach((port) => {
      const a = document.createElement("a");
      a.className = "port-row";
      a.href = "/p/" + port + "/";
      a.target = "_blank";
      a.rel = "noopener";
      // token in query if needed for new tab
      if (authToken) a.href += (a.href.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(authToken);
      a.innerHTML = `<span class="port-num">:${port}</span><span class="port-url">/p/${port}/</span>`;
      portsList.appendChild(a);
    });
  } catch (err) {
    portsList.innerHTML = '<div class="tree-empty">No se pudieron listar puertos.</div>';
  }
}

document.getElementById("refreshPorts")?.addEventListener("click", loadPorts);

// Auto-refresh ports cada 8s si el panel existe
setInterval(() => {
  if (document.getElementById("shell") && !document.getElementById("shell").hidden) {
    loadPorts();
  }
}, 8000);


// ------------------------------------------------------------------
// Boot
// ------------------------------------------------------------------
async function boot() {
  try {
    const status = await fetch("/api/auth-status").then((r) => r.json());
    authRequired = Boolean(status.required);
  } catch (_) {
    authRequired = false;
  }

  await runSplash();

  if (authRequired && !authToken) {
    showAuthOverlay();
    setStatus(false, "Esperando token…");
  } else {
    loadSessions();
  }
}

boot();
