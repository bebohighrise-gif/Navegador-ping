CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  command_line TEXT NOT NULL,
  cwd TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'stopped',
  port_internal INTEGER,
  env_vars_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exposures (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  type TEXT NOT NULL,
  generated_url TEXT,
  tunnel_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_history (
  id TEXT PRIMARY KEY,
  user_key_hash TEXT NOT NULL,
  command TEXT NOT NULL,
  cwd TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  exit_code INTEGER,
  output_ref TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  project_id TEXT,
  command_line TEXT NOT NULL,
  cron_expr TEXT NOT NULL,
  cwd TEXT NOT NULL,
  env_vars_json TEXT,
  last_run TEXT,
  next_run TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workspace_file_chunks (
  path TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(path, chunk_index)
);

-- Los archivos pequeños usan workspace_files; los grandes pueden fragmentarse
-- desde la API para no depender de un disco efímero de Render.

