#!/usr/bin/python3
"""Fixture tests for the local-transcript collectors.

Builds a fake $HOME holding one session per agent in the shape that agent
writes, runs each collector against it, and checks the record's totals.
Run: python3 tests/test_collectors.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLECTORS = ROOT / "collectors"
NOW_MS = int(time.time() * 1000)
NOW_ISO = datetime.now().astimezone().isoformat()
TODAY = datetime.now().strftime("%Y-%m-%d")


def write(path, content):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content if isinstance(content, str) else json.dumps(content))


def jsonl(*rows):
  return "\n".join(json.dumps(r) for r in rows) + "\n"


def pb_varint(n):
  out = bytearray()
  while True:
    byte, n = n & 0x7F, n >> 7
    out.append(byte | (0x80 if n else 0))
    if not n:
      return bytes(out)


def pb(*fields):
  """Encode (number, value) pairs: ints as varints, bytes/str as length-delimited."""
  out = b""
  for number, value in fields:
    if isinstance(value, int):
      out += pb_varint(number << 3) + pb_varint(value)
    else:
      value = value.encode() if isinstance(value, str) else value
      out += pb_varint(number << 3 | 2) + pb_varint(len(value)) + value
  return out


def build(home):
  share, config = home / ".local" / "share", home / ".config"

  # Gemini CLI: legacy single-document session (input includes cached).
  write(home / ".gemini/tmp/proj/chats/session-1.json", {
    "sessionId": "g1",
    "messages": [
      {"id": "u1", "type": "user", "timestamp": NOW_ISO, "content": "hi"},
      {"id": "m1", "type": "gemini", "timestamp": NOW_ISO, "model": "gemini-2.5-pro",
       "tokens": {"input": 1000, "output": 200, "cached": 400, "thoughts": 50, "tool": 0, "total": 1250}},
    ],
  })
  # Qwen Code: JSONL records with raw usageMetadata; m1 streams twice.
  write(home / ".qwen/projects/proj/chats/s.jsonl", jsonl(
    {"id": "m1", "type": "assistant", "sessionId": "q1", "timestamp": NOW_ISO, "model": "qwen3-coder",
     "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1}},
    {"id": "m1", "type": "assistant", "sessionId": "q1", "timestamp": NOW_ISO, "model": "qwen3-coder",
     "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 100, "cachedContentTokenCount": 100}},
  ))

  # opencode: SQLite message table plus a legacy per-message file.
  db = share / "opencode/opencode.db"
  db.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(db)
  conn.execute("CREATE TABLE message (id TEXT, session_id TEXT, data TEXT)")
  conn.execute("INSERT INTO message VALUES (?,?,?)", ("a", "s1", json.dumps({
    "role": "assistant", "providerID": "anthropic", "modelID": "claude-sonnet-5", "time": {"created": NOW_MS},
    "tokens": {"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 300, "write": 50}}})))
  conn.execute("INSERT INTO message VALUES (?,?,?)", ("b", "s1", json.dumps({"role": "user"})))
  conn.execute("INSERT INTO message VALUES (?,?,?)", ("c", "s1", "not json"))
  conn.commit()
  conn.close()
  write(share / "opencode/storage/message/s2/m.json", {
    "role": "assistant", "modelID": "gpt-5", "time": {"created": NOW_MS}, "tokens": {"input": 25, "output": 0}})

  # pi: session JSONL, one duplicated message id.
  msg = {"type": "message", "id": "p1", "timestamp": NOW_ISO,
         "message": {"role": "assistant", "model": "glm-5", "usage": {"input": 70, "output": 30, "cacheRead": 0, "cacheWrite": 0}}}
  write(home / ".pi/agent/sessions/x/s.jsonl", jsonl(msg, msg))

  # Amp thread.
  write(share / "amp/threads/T-1.json", {"id": "T-1", "created": NOW_MS, "messages": [
    {"role": "user"},
    {"role": "assistant", "usage": {"model": "claude-opus-5", "inputTokens": 10, "outputTokens": 90,
                                    "cacheReadInputTokens": 900, "cacheCreationInputTokens": 0}},
  ]})

  # Factory Droid session totals.
  write(home / ".factory/sessions/proj/abc.settings.json", {
    "model": "gpt-5-codex", "tokenUsage": {"inputTokens": 1000, "outputTokens": 100, "cacheReadTokens": 0,
                                           "cacheCreationTokens": 0, "thinkingTokens": 0}})

  # Goose sessions.db.
  db = share / "goose/sessions/sessions.db"
  db.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(db)
  conn.execute("CREATE TABLE sessions (id TEXT, created_at TEXT, updated_at TEXT, input_tokens INT, output_tokens INT,"
               " accumulated_input_tokens INT, accumulated_output_tokens INT, model_config_json TEXT)")
  conn.execute("INSERT INTO sessions VALUES ('g', ?, ?, 1, 1, 400, 100, ?)",
               (NOW_ISO, NOW_ISO, json.dumps({"model_name": "gpt-4.1"})))
  conn.commit()
  conn.close()

  # Crush project database found through projects.json.
  data_dir = home / "code/app/.crush"
  data_dir.mkdir(parents=True)
  conn = sqlite3.connect(data_dir / "crush.db")
  conn.execute("CREATE TABLE sessions (id TEXT, created_at INT, updated_at INT, prompt_tokens INT, completion_tokens INT, message_count INT)")
  conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, model TEXT)")
  conn.execute("INSERT INTO sessions VALUES ('c1', ?, ?, 300, 30, 4)", (NOW_MS // 1000, NOW_MS // 1000))
  conn.execute("INSERT INTO messages VALUES ('m', 'c1', 'kimi-k3')")
  conn.commit()
  conn.close()
  write(share / "crush/projects.json", {"projects": [{"path": str(home / "code/app"), "data_dir": str(data_dir)}]})

  # Cline-family task histories (Roo in VSCodium to cover another editor).
  def task(base, model):
    write(base / "task_metadata.json", {"model_usage": [{"model_id": model}]})
    write(base / "ui_messages.json", [
      {"ts": NOW_MS, "type": "say", "say": "text", "text": "hello"},
      {"ts": NOW_MS, "type": "say", "say": "api_req_started",
       "text": json.dumps({"tokensIn": 11, "tokensOut": 22, "cacheReads": 33, "cacheWrites": 44, "cost": 0.1})},
    ])
  task(config / "Code/User/globalStorage/saoudrizwan.claude-dev/tasks/1", "claude-sonnet-5")
  task(config / "VSCodium/User/globalStorage/rooveterinaryinc.roo-cline/tasks/1", "deepseek-v4")
  task(config / "Code/User/globalStorage/kilocode.kilo-code/tasks/1", "kilo-auto")

  # Antigravity (agy): protobuf ModelUsageStats in gen_metadata, timestamps
  # on the steps it points at. Step 0 is from 2020, so only step 1 is today.
  db = home / ".gemini/antigravity-cli/conversations/c1.db"
  db.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(db)
  conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, metadata BLOB)")
  conn.execute("CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)")
  conn.execute("INSERT INTO steps VALUES (0, ?)", (pb((1, pb((1, 1_600_000_000)))),))
  conn.execute("INSERT INTO steps VALUES (1, ?)", (pb((1, pb((1, NOW_MS // 1000)))),))
  def gen(step, usage):
    return pb((1, pb((4, pb(*usage)), (19, "gemini-3.8-flash"),
                     (20, pb((1, "last_step_index"), (2, str(step)))))))
  conn.execute("INSERT INTO gen_metadata VALUES (0, ?, 0)", (gen(0, [(1, 1318), (2, 999), (3, 999)]),))
  conn.execute("INSERT INTO gen_metadata VALUES (1, ?, 0)", (gen(1, [(1, 1318), (2, 100), (3, 50), (5, 1000), (6, 24)]),))
  conn.commit()
  conn.close()


# agent -> (total tokens today, model expected in modelUsage)
EXPECTED = {
  "gemini": (600 + 250 + 400, "gemini-2.5-pro"),  # fresh input + output/thoughts + cached
  "qwen": (400 + 100 + 100, "qwen3-coder"),
  "opencode": (100 + 25 + 300 + 50 + 25, "claude-sonnet-5"),
  "pi": (100, "glm-5"),
  "amp": (1000, "claude-opus-5"),
  "droid": (1100, "gpt-5-codex"),
  "goose": (500, "gpt-4.1"),
  "crush": (330, "kimi-k3"),
  "cline": (110, "claude-sonnet-5"),
  "roo": (110, "deepseek-v4"),
  "kilocode": (110, "kilo-auto"),
  "antigravity": (1150, "gemini-3.8-flash"),
}


def main():
  failures = 0
  with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    build(home)
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "XDG_CACHE_HOME": str(home / ".cache")}
    for agent, (tokens, model) in EXPECTED.items():
      proc = subprocess.run([str(COLLECTORS / agent), "--force"], capture_output=True, text=True, env=env)
      try:
        record = json.loads(proc.stdout)
        assert record["id"] == agent, "id"
        assert record["todayTotalTokens"] == tokens, f"todayTotalTokens {record['todayTotalTokens']} != {tokens}"
        assert model in record["modelUsage"], f"model {model} not in {list(record['modelUsage'])}"
        assert record["recentDays"][-1] == {"date": TODAY, "messageCount": tokens}, "recentDays"
        assert record["activeDays"] == (2 if agent == "antigravity" else 1), "activeDays"
        print(f"ok   {agent}")
      except Exception as exc:
        failures += 1
        print(f"FAIL {agent}: {exc}\n{proc.stdout[:400]}{proc.stderr[:800]}")

    # API collectors without credentials must still print an empty record.
    for agent in ("openrouter", "deepseek", "moonshot", "novita", "zai", "minimax", "ollama", "cursor"):
      proc = subprocess.run([str(COLLECTORS / agent)], capture_output=True, text=True, env=env)
      try:
        record = json.loads(proc.stdout)
        assert record["id"] == agent and record["ready"] is False and not record["limits"]
        print(f"ok   {agent} (no credentials)")
      except Exception as exc:
        failures += 1
        print(f"FAIL {agent}: {exc}\n{proc.stdout[:400]}{proc.stderr[:800]}")
  sys.exit(1 if failures else 0)


if __name__ == "__main__":
  main()
