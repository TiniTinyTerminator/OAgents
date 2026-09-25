#!/usr/bin/python3
"""API collectors against canned responses (shapes from the providers' docs
and ai-usagebar's captured fixtures). Run: python3 tests/test_api_collectors.py"""

import contextlib
import io
import json
import runpy
import sys
from pathlib import Path

COLLECTORS = Path(__file__).resolve().parent.parent / "collectors"
sys.path.insert(0, str(COLLECTORS))
import _agentusage  # noqa: E402

RESPONSES = {
  "https://openrouter.ai/api/v1/credits": {"data": {"total_credits": 20, "total_usage": 5.5}},
  "https://openrouter.ai/api/v1/key": {"data": {"limit": 10, "usage": 2.5, "is_free_tier": False}},
  "https://api.deepseek.com/user/balance": {"is_available": True, "balance_infos": [
    {"currency": "USD", "total_balance": "7.25", "granted_balance": "0", "topped_up_balance": "7.25"}]},
  "https://api.moonshot.ai/v1/users/me/balance": {"code": 0, "status": True, "data": {
    "available_balance": 49.5, "voucher_balance": 46.5, "cash_balance": 3}},
  "https://api.novita.ai/openapi/v1/billing/balance/detail": {"availableBalance": "123400", "cashBalance": "0",
                                                              "creditLimit": "0", "outstandingInvoices": "0"},
  "https://api.z.ai/api/monitor/usage/quota/limit": {"code": 200, "success": True, "data": {"level": "pro", "limits": [
    {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 42},
    {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 10, "nextResetTime": 1779792169974},
    {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 3}]}},
  "https://api.minimax.io/v1/token_plan/remains": {"base_resp": {"status_code": 0, "status_msg": "success"}, "model_remains": [
    {"model_name": "general", "start_time": 1779780000000, "end_time": 1779798000000, "current_interval_remaining_percent": 75,
     "weekly_start_time": 1779400000000, "weekly_end_time": 1780004800000, "current_weekly_remaining_percent": 90}]},
  "https://ollama.com/api/usage": {"limits": {"session": {"usage": 0.819, "models": []}, "weekly": {"usage": 0.23, "models": []}}},
}

EXPECTED = {
  "openrouter": lambda r: r["balance"]["remaining"] == 14.5 and r["balance"]["funded"] == 20 and r["limits"][0]["percent"] == 0.25,
  "deepseek": lambda r: r["balance"]["remaining"] == 7.25,
  "moonshot": lambda r: r["balance"]["remaining"] == 49.5,
  "novita": lambda r: abs(r["balance"]["remaining"] - 12.34) < 1e-9,
  "zai": lambda r: [l["title"] for l in r["limits"]] == ["Session", "Weekly", "MCP (monthly)"] and r["limits"][0]["percent"] == 0.42
                   and r["tierLabel"] == "GLM Coding Pro",
  "minimax": lambda r: r["limits"][0]["label"] == "5h window" and r["limits"][0]["percent"] == 0.25 and r["limits"][1]["percent"] == 0.1,
  "ollama": lambda r: [l["percent"] for l in r["limits"]] == [0.819, 0.23],
}


def main():
  _agentusage.http_json = lambda url, *a, **k: json.loads(json.dumps(RESPONSES[url]))
  _agentusage.api_key = lambda *a, **k: "test-key"
  failures = 0
  for agent, check in EXPECTED.items():
    out = io.StringIO()
    sys.argv = [agent]
    try:
      with contextlib.redirect_stdout(out):
        runpy.run_path(str(COLLECTORS / agent), run_name="__main__")
      record = json.loads(out.getvalue())
      assert record["usageStatusText"] == "", record["authHelpText"]
      assert check(record), json.dumps(record)
      print(f"ok   {agent}")
    except Exception as exc:
      failures += 1
      print(f"FAIL {agent}: {exc}")
  sys.exit(1 if failures else 0)


if __name__ == "__main__":
  main()
