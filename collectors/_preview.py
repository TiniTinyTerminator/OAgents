#!/usr/bin/python3
"""Write sample usage records for preview mode (reviews, demos, screenshots).

Nothing is read from disk or the network. Dates are relative to today, so
the weekly chart and "today" always look current.
Usage: _preview.py <output dir>
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime.now(timezone.utc)


def at(hours):
  return (NOW + timedelta(hours=hours)).isoformat()


def days(week):
  today = datetime.now()
  return [{"date": (today - timedelta(days=6 - i)).strftime("%Y-%m-%d"), "messageCount": int(v)} for i, v in enumerate(week)]


def tokens(inp, out, read, write=0):
  return {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read, "cacheCreationInputTokens": write}


def record(agent_id, name, week, models, limits=(), tier="", balance=None, prompts=0, sessions=0):
  model_totals = {m: sum(t.values()) for m, t in models.items()}
  scale = week[-1] / max(1, sum(model_totals.values()))
  return {
    "schemaVersion": 1, "id": agent_id, "name": name, "updatedAt": NOW.isoformat(),
    "ready": True, "hasLocalStats": bool(models), "tierLabel": tier,
    "usageStatusText": "", "authHelpText": "", "limits": list(limits), "balance": balance,
    "todayPrompts": prompts, "todaySessions": sessions, "todayTotalTokens": int(week[-1]),
    "todayTokensByModel": {m: int(v * scale) for m, v in model_totals.items()},
    "recentDays": days(week), "totalPrompts": prompts * 40, "totalSessions": sessions * 30,
    "activeDays": 58 if models else 0, "activeDates": [], "modelUsage": models,
  }


def limit(label, percent, hours, title=""):
  entry = {"label": label, "percent": percent, "resetsAt": at(hours)}
  if title:
    entry["title"] = title
  return entry


SAMPLES = [
  record("claude", "Claude Code", [42e6, 18e6, 61e6, 0, 77e6, 95e6, 54e6],
         {"claude-opus-5-5": tokens(310_000, 4_100_000, 1_620_000_000, 28_000_000),
          "claude-sonnet-5": tokens(90_000, 1_300_000, 240_000_000, 9_000_000),
          "claude-haiku-4-5": tokens(40_000, 220_000, 31_000_000, 1_000_000)},
         [limit("Session (5-hour)", 0.34, 3.2), limit("Weekly (7-day)", 0.58, 70)], "Max 5x", prompts=64, sessions=5),
  record("codex", "Codex", [8e6, 12e6, 0, 3e6, 22e6, 17e6, 9e6],
         {"gpt-5.6": tokens(4_200_000, 900_000, 160_000_000), "gpt-5.6-mini": tokens(800_000, 200_000, 22_000_000)},
         [limit("5h window", 0.12, 4.1), limit("Weekly (7-day)", 0.41, 101)], "plus", prompts=18, sessions=2),
  record("gemini", "Gemini CLI", [2e6, 0, 5e6, 1e6, 0, 3e6, 4e6],
         {"gemini-3-pro": tokens(2_900_000, 400_000, 8_000_000), "gemini-3-flash": tokens(900_000, 120_000, 1_500_000)},
         prompts=9, sessions=1),
  record("copilot", "Copilot", [0] * 7, {},
         [limit("Premium requests (monthly)", 0.46, 140, "Premium requests"), limit("Chat (monthly)", 0.12, 140, "Chat")], "Pro"),
  record("opencode", "opencode", [6e6, 9e6, 4e6, 0, 11e6, 7e6, 13e6],
         {"kimi-k3": tokens(3_300_000, 600_000, 41_000_000), "glm-5.3": tokens(1_700_000, 300_000, 12_000_000),
          "deepseek-v4": tokens(900_000, 150_000, 6_000_000)}, prompts=22, sessions=3),
  record("zai", "Z.ai", [0] * 7, {},
         [limit("Session (5-hour)", 0.21, 2.5, "Session"), limit("Weekly (7-day)", 0.09, 120, "Weekly"),
          limit("MCP calls (monthly)", 0.03, 400, "MCP (monthly)")], "GLM Coding Pro"),
  record("openrouter", "OpenRouter", [0] * 7, {}, tier="Prepaid",
         balance={"remaining": 14.62, "funded": 25.0, "spent": 10.38, "currency": "USD", "estimated": False}),
  record("amp", "Amp", [1e6, 0, 0, 2e6, 0, 1e6, 3e6],
         {"claude-opus-5-5": tokens(120_000, 400_000, 6_000_000)}, prompts=7, sessions=1),
]


def main():
  out = Path(sys.argv[1])
  out.mkdir(parents=True, exist_ok=True)
  for sample in SAMPLES:
    (out / f"{sample['id']}.json").write_text(json.dumps(sample) + "\n")


if __name__ == "__main__":
  main()
