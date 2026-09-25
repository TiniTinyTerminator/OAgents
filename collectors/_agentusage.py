"""Shared plumbing for the ttt.agents collectors.

Every collector prints one display-ready JSON record in the same contract the
stock Omarchy collectors use (see omarchy-agent-usage-claude/codex). This
module owns the parts they all share: the token accumulator, the scan cache,
credential lookup, and a tiny HTTP helper. Collectors only describe where
their numbers live.
"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
XDG_CACHE = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")

# A scan this recent is only reused to dedup concurrent collector runs;
# --limits-only (the panel opening) only needs fresh limits and may reuse a
# scan for 15 minutes.
SCAN_REUSE_SECONDS = 20
LIMITS_ONLY_REUSE_SECONDS = 900
HTTP_TIMEOUT = 8


# ------------------------------------------------------------------ values

def number(value):
  try:
    n = int(float(value or 0))
    return n if n > 0 else 0
  except Exception:
    return 0


def local_day(value, fallback=None):
  """A local YYYY-MM-DD from epoch seconds/milliseconds or an ISO string."""
  if value is None or value == "":
    value = fallback
  if value is None or value == "":
    return None
  try:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().lstrip("-").replace(".", "", 1).isdigit()):
      value = float(value)
      if value > 10_000_000_000:
        value = value / 1000
      return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
    text = str(value).strip()
    if text.endswith("Z"):
      text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
      dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d")
  except Exception:
    return None


def iso_from(value):
  """ISO-8601 UTC from epoch seconds/milliseconds or an ISO string; "" if unknown."""
  if value in (None, "", 0):
    return ""
  try:
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
      value = float(value)
      if value > 10_000_000_000:
        value = value / 1000
      return datetime.fromtimestamp(value, timezone.utc).isoformat()
    text = str(value).strip()
    if text.endswith("Z"):
      text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
      dt = dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat()
  except Exception:
    return ""


def model_name(raw, fallback="unknown"):
  value = str(raw or "").strip().rstrip("/").split("/")[-1]
  return value or fallback


# ------------------------------------------------------------- accumulator

class Usage:
  """Aggregates token usage into the record's local-stats fields."""

  def __init__(self):
    now = datetime.now()
    self.today = now.strftime("%Y-%m-%d")
    self.recent_dates = [(now - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(6, -1, -1)]
    self.recent = {day: 0 for day in self.recent_dates}
    self.today_tokens_by_model = {}
    self.model_usage = {}
    self.today_sessions = set()
    self.sessions = set()
    self.active_days = set()
    self.today_prompts = 0
    self.today_total_tokens = 0
    self.total_prompts = 0

  def add(self, day, session, model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0, prompts=1):
    input_tokens, output_tokens = number(input_tokens), number(output_tokens)
    cache_read, cache_write = number(cache_read), number(cache_write)
    total = input_tokens + output_tokens + cache_read + cache_write
    if total == 0 or not day:
      return False
    model = model_name(model)
    self.total_prompts += prompts
    self.sessions.add(session)
    self.active_days.add(day)

    bucket = self.model_usage.setdefault(model, {
      "inputTokens": 0,
      "outputTokens": 0,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
    })
    bucket["inputTokens"] += input_tokens
    bucket["outputTokens"] += output_tokens
    bucket["cacheReadInputTokens"] += cache_read
    bucket["cacheCreationInputTokens"] += cache_write

    if day in self.recent:
      self.recent[day] += total
    if day == self.today:
      self.today_prompts += prompts
      self.today_sessions.add(session)
      self.today_total_tokens += total
      self.today_tokens_by_model[model] = self.today_tokens_by_model.get(model, 0) + total
    return True

  def stats(self):
    return {
      "todayPrompts": self.today_prompts,
      "todaySessions": len(self.today_sessions),
      "todayTotalTokens": self.today_total_tokens,
      "todayTokensByModel": self.today_tokens_by_model,
      "recentDays": [{"date": day, "messageCount": self.recent[day]} for day in self.recent_dates],
      "totalPrompts": self.total_prompts,
      "totalSessions": len(self.sessions),
      "activeDays": len(self.active_days),
      "activeDates": sorted(self.active_days),
      "modelUsage": self.model_usage,
    }


# ------------------------------------------------------------------- cache

def _write_json(path, payload):
  fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
  tmp = Path(tmp_name)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)
  except BaseException:
    tmp.unlink(missing_ok=True)
    raise


def _read_cache(path, max_age, today):
  if max_age <= 0 or not path.exists():
    return None
  try:
    age = time.time() - path.stat().st_mtime
    if not (0 <= age <= max_age):
      return None
    cached = json.loads(path.read_text(encoding="utf-8"))
  except Exception:
    return None
  # today* fields only mean "today" on the day they were scanned.
  if not isinstance(cached, dict) or cached.get("schemaVersion") != 1 or cached.get("scanDate") != today:
    return None
  return cached.get("stats") if isinstance(cached.get("stats"), dict) else None


def cached_scan(agent_id, scan, max_age, key=""):
  """Run scan() -> (stats, complete), reusing a recent result.

  The cache is an optimization only: any failure in it degrades to a
  direct scan, never to a failed collector.
  """
  today = datetime.now().strftime("%Y-%m-%d")
  try:
    root = XDG_CACHE / "ttt" / "agent-usage"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1((str(HOME) + "\n" + key).encode("utf-8")).hexdigest()[:16]
    cache_file = root / f"{agent_id}-scan-{digest}.json"
    lock_file = root / f"{agent_id}-scan-{digest}.lock"
    cached = _read_cache(cache_file, max_age, today)
    if cached is not None:
      return cached
    with lock_file.open("w") as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      cached = _read_cache(cache_file, max_age, today)
      if cached is not None:
        return cached
      stats, complete = scan()
      if complete:
        try:
          _write_json(cache_file, {"schemaVersion": 1, "scanDate": today, "stats": stats})
        except Exception as exc:
          warn(agent_id, f"could not write usage cache ({exc})")
      return stats
  except Exception as exc:
    warn(agent_id, f"cache unavailable ({exc}); scanning directly")
    stats, _ = scan()
    return stats


# ------------------------------------------------------------- credentials

def warn(agent_id, message):
  print(f"ttt-agent-usage-{agent_id}: {message}", file=sys.stderr)


def read_json(path):
  try:
    return json.loads(Path(path).read_text(encoding="utf-8"))
  except Exception:
    return None


def agent_config(agent_id):
  """This agent's block from ~/.config/ttt/agents.json (may be empty)."""
  config = read_json(XDG_CONFIG / "ttt" / "agents.json")
  block = config.get(agent_id) if isinstance(config, dict) else None
  return block if isinstance(block, dict) else {}


def opencode_key(*provider_ids):
  """An API key opencode stored for any of these provider ids."""
  auth = read_json(XDG_DATA / "opencode" / "auth.json")
  if not isinstance(auth, dict):
    return ""
  for pid in provider_ids:
    entry = auth.get(pid)
    if isinstance(entry, dict) and entry.get("type", "api") == "api":
      key = str(entry.get("key") or "").strip()
      if key:
        return key
  return ""


def api_key(agent_id, env_names=(), opencode_ids=()):
  """Env var first, then ~/.config/ttt/agents.json, then opencode's auth.json."""
  for name in env_names:
    value = os.environ.get(name, "").strip()
    if value:
      return value
  value = str(agent_config(agent_id).get("apiKey") or "").strip()
  if value:
    return value
  return opencode_key(*opencode_ids) if opencode_ids else ""


def runtime_env():
  env = os.environ.copy()
  parts = [env.get("PATH", ""), f"{HOME}/.local/bin", f"{HOME}/.npm-global/bin", f"{HOME}/.local/share/mise/shims", "/usr/local/bin", "/usr/bin"]
  env["PATH"] = os.pathsep.join(part for part in parts if part)
  return env


def find_command(name):
  return shutil.which(name, path=runtime_env().get("PATH"))


def run_command(argv, timeout=5):
  try:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=runtime_env())
    return proc.stdout if proc.returncode == 0 else ""
  except Exception:
    return ""


# -------------------------------------------------------------------- http

class HttpError(Exception):
  def __init__(self, status, message):
    super().__init__(message)
    self.status = status


def http_json(url, headers=None, method="GET", body=None, timeout=HTTP_TIMEOUT):
  data = json.dumps(body).encode("utf-8") if body is not None else None
  request = urllib.request.Request(url, data=data, method=method)
  request.add_header("Accept", "application/json")
  request.add_header("User-Agent", "ttt-agent-usage/1")
  if data is not None:
    request.add_header("Content-Type", "application/json")
  for key, value in (headers or {}).items():
    request.add_header(key, value)
  try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
      return json.loads(response.read().decode("utf-8") or "null")
  except urllib.error.HTTPError as exc:
    raise HttpError(exc.code, f"HTTP {exc.code}") from None
  except urllib.error.URLError as exc:
    raise HttpError(0, f"network unreachable ({exc.reason})") from None


def limit(label, percent, resets_at="", title=""):
  """One limit window; percent is 0..1 (values over 1 mean over the allowance)."""
  entry = {"label": label, "percent": max(0.0, float(percent)), "resetsAt": iso_from(resets_at)}
  if title:
    entry["title"] = title
  return entry


def balance(remaining, funded=0, spent=0, currency="USD", estimated=False):
  return {
    "remaining": max(0.0, float(remaining)),
    "funded": max(0.0, float(funded or 0)),
    "spent": max(0.0, float(spent or 0)),
    "currency": currency,
    "estimated": bool(estimated),
  }


# ------------------------------------------------------------------ record

def parse_args():
  parser = argparse.ArgumentParser()
  parser.add_argument("--force", action="store_true")
  parser.add_argument("--limits-only", action="store_true")
  return parser.parse_args()


def max_age(args):
  return 0 if args.force else (LIMITS_ONLY_REUSE_SECONDS if args.limits_only else SCAN_REUSE_SECONDS)


def emit(agent_id, name, stats=None, remote=None, **extra):
  """Print the record. stats: local-stats dict or None; remote: limits/balance fields."""
  record = {
    "schemaVersion": 1,
    "id": agent_id,
    "name": name,
    "updatedAt": datetime.now(timezone.utc).isoformat(),
    "ready": True,
    "hasLocalStats": stats is not None,
  }
  if stats is not None:
    record.update(stats)
  record.update({"limits": [], "tierLabel": "", "usageStatusText": "", "authHelpText": ""})
  if remote:
    record.update(remote)
  record.update(extra)
  print(json.dumps(record, separators=(",", ":")))


def sqlite_ro(path):
  return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def table_columns(conn, table):
  try:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
  except sqlite3.Error:
    return set()


def remote_record(agent_id, name, credential, fetch, auth_help, stats=None):
  """Emit a record for an agent whose numbers come from an API.

  No credential means the agent is not set up here: the record carries no
  data, so the panel leaves it out entirely. fetch(credential) returns the
  limits/balance/tierLabel fields; failures become a status line instead of
  a failed collector.
  """
  if not credential:
    emit(agent_id, name, stats, ready=False, authHelpText=auth_help)
    return
  try:
    remote = fetch(credential)
  except HttpError as exc:
    status = exc.status
    remote = {
      "usageStatusText": f"{name} sign-in rejected" if status in (401, 403) else f"{name} usage unavailable",
      "authHelpText": auth_help if status in (401, 403) else str(exc),
    }
    # Unreachable, not refused: ask the panel for one sooner retry.
    if status == 0:
      remote["retryAdvised"] = True
  except Exception as exc:
    remote = {"usageStatusText": f"{name} usage unavailable", "authHelpText": f"Unexpected response ({exc})"}
  emit(agent_id, name, stats, remote)
