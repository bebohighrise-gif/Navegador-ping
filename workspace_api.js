const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const MAX_PREVIEW_BYTES = 200 * 1024;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const TEXT_EXTENSIONS = new Set([
  ".txt", ".md", ".json", ".js", ".jsx", ".ts", ".tsx", ".py", ".sh",
  ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env", ".html",
  ".css", ".scss", ".sql", ".go", ".rb", ".php", ".java", ".c", ".h",
  ".cpp", ".hpp", ".rs", ".gitignore", ".dockerfile", ".xml", ".csv",
  ".log", ".vue", ".svelte", ".kt", ".swift", ".r", ".lua", ".pl",
]);

function safeResolve(root, relPath) {
  const cleaned = (relPath || "").replace(/^\/+/, "");
  const resolved = path.resolve(root, cleaned);
  const rootWithSep = root.endsWith(path.sep) ? root : root + path.sep;
  if (resolved !== root && !resolved.startsWith(rootWithSep)) {
    return null;
  }
  return resolved;
}

function isLikelyText(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (TEXT_EXTENSIONS.has(ext)) return true;
  if (!ext) return true;
  return false;
}

function listProjects(workspaceRoot) {
  if (!fs.existsSync(workspaceRoot)) return [];
  return fs
    .readdirSync(workspaceRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => {
      const full = path.join(workspaceRoot, entry.name);
      const stat = fs.statSync(full);
      let fileCount = 0;
      try { fileCount = fs.readdirSync(full).length; } catch (_) { fileCount = 0; }
      return { name: entry.name, updatedAt: stat.mtime.toISOString(), itemCount: fileCount };
    })
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function listDirectory(workspaceRoot, relPath) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (!fs.existsSync(target)) return { error: "not_found" };
  const stat = fs.statSync(target);
  if (!stat.isDirectory()) return { error: "not_a_directory" };
  const entries = fs
    .readdirSync(target, { withFileTypes: true })
    .filter((entry) => !entry.name.startsWith("."))
    .map((entry) => {
      const entryFull = path.join(target, entry.name);
      const entryStat = fs.statSync(entryFull);
      return {
        name: entry.name,
        type: entry.isDirectory() ? "dir" : "file",
        size: entry.isDirectory() ? null : entryStat.size,
      };
    })
    .sort((a, b) => {
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  return { entries };
}

function readFilePreview(workspaceRoot, relPath) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (!fs.existsSync(target)) return { error: "not_found" };
  const stat = fs.statSync(target);
  if (stat.isDirectory()) return { error: "not_a_file" };
  if (stat.size > MAX_PREVIEW_BYTES) return { error: "too_large", size: stat.size, limit: MAX_PREVIEW_BYTES };
  if (!isLikelyText(target)) return { error: "binary" };
  const content = fs.readFileSync(target, "utf8");
  return { content, size: stat.size, ext: path.extname(target).toLowerCase() };
}

function createEntry(workspaceRoot, relPath, type, content = "") {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (fs.existsSync(target)) return { error: "already_exists" };
  const parent = path.dirname(target);
  if (!fs.existsSync(parent) || !fs.statSync(parent).isDirectory()) return { error: "parent_not_found" };
  try {
    if (type === "dir") fs.mkdirSync(target, { recursive: false });
    else fs.writeFileSync(target, content ?? "", "utf8");
    return { ok: true, path: relPath, type };
  } catch (err) {
    return { error: "write_failed", message: err.message };
  }
}

function renameEntry(workspaceRoot, fromRel, toRel) {
  const from = safeResolve(workspaceRoot, fromRel);
  const to = safeResolve(workspaceRoot, toRel);
  if (!from || !to) return { error: "invalid_path" };
  if (!fs.existsSync(from)) return { error: "not_found" };
  if (fs.existsSync(to)) return { error: "already_exists" };
  if (from === path.resolve(workspaceRoot)) return { error: "forbidden" };
  try {
    fs.renameSync(from, to);
    return { ok: true, from: fromRel, to: toRel };
  } catch (err) {
    return { error: "rename_failed", message: err.message };
  }
}

function deleteEntry(workspaceRoot, relPath) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (!fs.existsSync(target)) return { error: "not_found" };
  if (target === path.resolve(workspaceRoot)) return { error: "forbidden" };
  try {
    const stat = fs.statSync(target);
    if (stat.isDirectory()) fs.rmSync(target, { recursive: true, force: true });
    else fs.unlinkSync(target);
    return { ok: true, path: relPath };
  } catch (err) {
    return { error: "delete_failed", message: err.message };
  }
}

function writeFileContent(workspaceRoot, relPath, content) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  const parent = path.dirname(target);
  if (!fs.existsSync(parent)) return { error: "parent_not_found" };
  try {
    fs.writeFileSync(target, content ?? "", "utf8");
    return { ok: true, path: relPath, size: Buffer.byteLength(content ?? "", "utf8") };
  } catch (err) {
    return { error: "write_failed", message: err.message };
  }
}

function saveUploadedFile(workspaceRoot, relPath, buffer) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (buffer.length > MAX_UPLOAD_BYTES) return { error: "too_large", limit: MAX_UPLOAD_BYTES };
  const parent = path.dirname(target);
  try {
    fs.mkdirSync(parent, { recursive: true });
    fs.writeFileSync(target, buffer);
    return { ok: true, path: relPath, size: buffer.length };
  } catch (err) {
    return { error: "write_failed", message: err.message };
  }
}

function extractZip(workspaceRoot, destRel, zipBuffer) {
  const dest = safeResolve(workspaceRoot, destRel || "");
  if (!dest) return { error: "invalid_path" };
  if (!fs.existsSync(dest)) {
    try { fs.mkdirSync(dest, { recursive: true }); } catch (err) {
      return { error: "mkdir_failed", message: err.message };
    }
  }
  const tmpZip = path.join("/tmp", "bebo-upload-" + Date.now() + ".zip");
  try {
    fs.writeFileSync(tmpZip, zipBuffer);
    execFileSync("unzip", ["-o", "-q", tmpZip, "-d", dest], { timeout: 60000 });
    return { ok: true, path: destRel || "." };
  } catch (err) {
    return { error: "extract_failed", message: err.message };
  } finally {
    try { fs.unlinkSync(tmpZip); } catch (_) {}
  }
}

function createZipBuffer(workspaceRoot, relPath) {
  const target = safeResolve(workspaceRoot, relPath || "");
  if (!target) return { error: "invalid_path" };
  if (!fs.existsSync(target)) return { error: "not_found" };
  const tmpZip = path.join("/tmp", "bebo-dl-" + Date.now() + ".zip");
  try {
    const cwd = path.dirname(target);
    const base = path.basename(target);
    execFileSync("zip", ["-r", "-q", tmpZip, base], { cwd, timeout: 120000 });
    const buf = fs.readFileSync(tmpZip);
    return { ok: true, buffer: buf, name: base + ".zip" };
  } catch (err) {
    return { error: "zip_failed", message: err.message };
  } finally {
    try { fs.unlinkSync(tmpZip); } catch (_) {}
  }
}

function readFileForDownload(workspaceRoot, relPath) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (!fs.existsSync(target)) return { error: "not_found" };
  const stat = fs.statSync(target);
  if (stat.isDirectory()) return { error: "not_a_file" };
  if (stat.size > MAX_UPLOAD_BYTES) return { error: "too_large", limit: MAX_UPLOAD_BYTES };
  try {
    const buffer = fs.readFileSync(target);
    return { ok: true, buffer, name: path.basename(target), size: stat.size };
  } catch (err) {
    return { error: "read_failed", message: err.message };
  }
}

function findProjectLogs(workspaceRoot, projectName) {
  const root = safeResolve(workspaceRoot, projectName);
  if (!root || !fs.existsSync(root)) return { error: "not_found" };
  const candidates = [];
  const walk = (dir, depth) => {
    if (depth > 3) return;
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { return; }
    for (const e of entries) {
      if (e.name.startsWith(".")) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (["node_modules", "vendor", ".git", "__pycache__"].includes(e.name)) continue;
        walk(full, depth + 1);
      } else if (/\\.(log|out)$/i.test(e.name) || e.name === "nohup.out") {
        try {
          const st = fs.statSync(full);
          candidates.push({
            path: path.relative(workspaceRoot, full),
            name: e.name,
            size: st.size,
            mtime: st.mtime.toISOString(),
          });
        } catch (_) {}
      }
    }
  };
  walk(root, 0);
  candidates.sort((a, b) => b.mtime.localeCompare(a.mtime));
  return { logs: candidates };
}

function tailLog(workspaceRoot, relPath, maxBytes = 64 * 1024) {
  const target = safeResolve(workspaceRoot, relPath);
  if (!target) return { error: "invalid_path" };
  if (!fs.existsSync(target)) return { error: "not_found" };
  const stat = fs.statSync(target);
  if (stat.isDirectory()) return { error: "not_a_file" };
  try {
    const fd = fs.openSync(target, "r");
    const size = stat.size;
    const start = Math.max(0, size - maxBytes);
    const len = size - start;
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, start);
    fs.closeSync(fd);
    let text = buf.toString("utf8");
    if (start > 0) {
      const nl = text.indexOf("\\n");
      if (nl >= 0) text = text.slice(nl + 1);
    }
    return { content: text, size, truncated: start > 0 };
  } catch (err) {
    return { error: "read_failed", message: err.message };
  }
}

module.exports = {
  safeResolve, listProjects, listDirectory, readFilePreview,
  createEntry, renameEntry, deleteEntry, writeFileContent,
  saveUploadedFile, extractZip, createZipBuffer, readFileForDownload,
  findProjectLogs, tailLog, MAX_PREVIEW_BYTES, MAX_UPLOAD_BYTES,
};
