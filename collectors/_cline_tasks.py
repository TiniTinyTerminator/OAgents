"""Cline-family task histories, shared by the cline, roo and kilocode collectors.

These VS Code extensions keep every task under
<editor config>/User/globalStorage/<extension id>/tasks/<task id>/ with a
ui_messages.json whose `api_req_started` entries carry a JSON text payload
of tokensIn / tokensOut / cacheReads / cacheWrites per API request.
"""

import json
from pathlib import Path

from _agentusage import HOME, XDG_CONFIG, local_day

EDITORS = ["Code", "Code - OSS", "Code - Insiders", "VSCodium", "Cursor", "Windsurf", "Kiro", "Trae", "Positron"]


def task_roots(extension_ids, extra=()):
  roots = []
  for editor in EDITORS:
    for ext in extension_ids:
      roots.append(XDG_CONFIG / editor / "User" / "globalStorage" / ext / "tasks")
  roots += [Path(p) for p in extra]
  return [r for r in roots if r.is_dir()]


def _task_model(task_dir, fallback):
  try:
    meta = json.loads((task_dir / "task_metadata.json").read_text(encoding="utf-8"))
    used = meta.get("model_usage") or []
    if used:
      return used[-1].get("model_id") or fallback
  except Exception:
    pass
  return fallback


def scan(usage, roots, fallback_model):
  for root in roots:
    for task_dir in root.iterdir():
      ui = task_dir / "ui_messages.json"
      if not ui.is_file():
        continue
      try:
        messages = json.loads(ui.read_text(encoding="utf-8"))
      except Exception:
        continue
      model = _task_model(task_dir, fallback_model)
      for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("say") != "api_req_started":
          continue
        try:
          info = json.loads(message.get("text") or "{}")
        except Exception:
          continue
        if not isinstance(info, dict):
          continue
        usage.add(local_day(message.get("ts")), str(task_dir), info.get("model") or model,
                  info.get("tokensIn"), info.get("tokensOut"), info.get("cacheReads"), info.get("cacheWrites"))
  return True
