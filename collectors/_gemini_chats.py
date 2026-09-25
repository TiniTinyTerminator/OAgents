"""Gemini CLI chat recordings, shared by the gemini and qwen collectors.

Gemini CLI (and its fork Qwen Code) record each session under
<home>/tmp/<project>/chats/ — a session-*.json document with a `messages`
array in older builds, one JSON record per line in newer ones. Assistant
turns carry either a `tokens` block (input/output/cached/thoughts/tool) or
the raw API `usageMetadata`.
"""

import json
from pathlib import Path

from _agentusage import local_day, number


def _tokens(entry):
  tokens = entry.get("tokens")
  if isinstance(tokens, dict):
    prompt = number(tokens.get("input")) + number(tokens.get("tool"))
    cached = number(tokens.get("cached"))
    output = number(tokens.get("output")) + number(tokens.get("thoughts"))
    if not (prompt or output) and number(tokens.get("total")):
      prompt = number(tokens.get("total"))
    return max(0, prompt - cached), output, cached
  meta = entry.get("usageMetadata") or (entry.get("message") or {}).get("usageMetadata")
  if isinstance(meta, dict):
    prompt = number(meta.get("promptTokenCount")) + number(meta.get("toolUsePromptTokenCount"))
    cached = number(meta.get("cachedContentTokenCount"))
    output = number(meta.get("candidatesTokenCount")) + number(meta.get("thoughtsTokenCount"))
    return max(0, prompt - cached), output, cached
  return 0, 0, 0


def _entries(path):
  text = path.read_text(encoding="utf-8", errors="replace")
  stripped = text.lstrip()
  if stripped.startswith("{") and path.suffix == ".json":
    try:
      doc = json.loads(text)
      if isinstance(doc, dict):
        return doc.get("messages") or [], doc.get("sessionId") or str(path)
    except Exception:
      pass
  entries = []
  session = str(path)
  for raw in text.splitlines():
    try:
      entry = json.loads(raw)
    except Exception:
      continue
    if isinstance(entry, dict):
      entries.append(entry)
      session = entry.get("sessionId") or session
  return entries, session


def scan(usage, roots, default_model):
  files = []
  for root in roots:
    root = Path(root)
    if root.is_dir():
      files += [p for p in root.glob("*/chats/*") if p.suffix in (".json", ".jsonl")]
  for path in files:
    try:
      entries, session = _entries(path)
      mtime = path.stat().st_mtime
    except Exception:
      continue
    # Newer recordings rewrite a message line as it streams; the last copy
    # of an id is the finished one.
    latest = {}
    for index, entry in enumerate(entries):
      if not isinstance(entry, dict):
        continue
      if str(entry.get("type") or entry.get("role") or "") not in ("gemini", "assistant", "model"):
        continue
      latest[str(entry.get("id") or index)] = entry
    for entry in latest.values():
      fresh, output, cached = _tokens(entry)
      day = local_day(entry.get("timestamp"), mtime)
      usage.add(day, "chat:" + str(session), entry.get("model") or default_model, fresh, output, cached, 0)
  return True
