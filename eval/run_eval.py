"""Runs cases.json through the assistant and scores each answer.

    cd backend
    .venv/bin/python ../eval/run_eval.py              # uses ANTHROPIC_API_KEY if set, else the stub
    ASSISTANT_MODE=stub .venv/bin/python ../eval/run_eval.py

Checks per case, each pass/fail, only where the case defines them:
  status     answered / declined matches
  route      required sources were used; forbidden ones were not
  numbers    every expected figure appears in the answer (0.5% tolerance)
  mentions   each group has at least one phrase present (case-insensitive)
  absent     none of these phrases appear
  citations  expected policy sections are in the evidence
  grounded   the backend's grounding check passed (no untraceable figures)
  no_numbers the answer contains no quantities at all (for must-decline cases)

The stub scores well on the cases it has rules for and fails the guardrail cases:
it shows the harness works, not that the stub is good. Results land in eval/results/.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.grounding import extract_numbers  # noqa: E402
from app.main import assistant  # noqa: E402
from app.engines import EngineError  # noqa: E402


def score(case: dict, resp: dict) -> dict[str, bool]:
    exp = case["expect"]
    text = (resp["answer"] + "\n" + "\n".join(resp["caveats"]) + "\n" + (resp["missing_information"] or ""))
    low = text.lower()
    checks: dict[str, bool] = {}
    if "status" in exp:
        checks["status"] = resp["status"] == exp["status"]
    if "sources_required" in exp or "sources_forbidden" in exp:
        observed = set(resp["route"]["observed"])
        checks["route"] = set(exp.get("sources_required", [])) <= observed and not (set(exp.get("sources_forbidden", [])) & observed)
    if "numbers" in exp:
        found = [v for _, v in extract_numbers(text)]
        checks["numbers"] = all(any(abs(f - n) <= max(0.01, 0.005 * abs(n)) for f in found) for n in exp["numbers"])
    if "mentions" in exp:
        checks["mentions"] = all(any(p.lower() in low for p in group) for group in exp["mentions"])
    if "absent" in exp:
        checks["absent"] = not any(p.lower() in low for p in exp["absent"])
    if "sections" in exp:
        cited = {p["section_id"] for p in resp["evidence"]["policy"]}
        checks["citations"] = set(exp["sections"]) <= cited
    if exp.get("numbers_forbidden"):
        checks["no_numbers"] = not extract_numbers(resp["answer"])
    checks["grounded"] = resp["grounding"]["passed"]
    return checks


def main() -> None:
    cases = json.loads((ROOT / "cases.json").read_text())
    mode = assistant.engine.mode
    print(f"Mode: {mode}{'  (STUB: rule-based, no LLM)' if mode == 'stub' else ''}\n")
    rows, total_checks, total_passed, full_passes = [], 0, 0, 0
    for case in cases:
        t0 = time.monotonic()
        try:
            resp = assistant.ask(case["question"], []).model_dump()
            checks = score(case, resp)
            error = None
        except EngineError as e:
            resp, checks, error = None, {"error": False}, f"{e.code}: {e.message}"
        passed = sum(checks.values())
        total_checks += len(checks)
        total_passed += passed
        full_passes += passed == len(checks)
        failed = [k for k, v in checks.items() if not v]
        print(f"{'PASS' if not failed else 'FAIL'}  {case['id']:<32} {passed}/{len(checks)}"
              f"{'  failed: ' + ', '.join(failed) if failed else ''}{'  ' + error if error else ''}")
        rows.append({"id": case["id"], "question": case["question"], "checks": checks, "error": error,
                     "seconds": round(time.monotonic() - t0, 2),
                     "status": resp and resp["status"], "answer": resp and resp["answer"],
                     "route": resp and resp["route"], "grounding": resp and resp["grounding"]})

    print(f"\nCases fully passed: {full_passes}/{len(cases)}   Checks passed: {total_passed}/{total_checks}")
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{mode}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"mode": mode, "model": getattr(assistant.engine, "model", None),
                               "cases_passed": full_passes, "cases": len(cases),
                               "checks_passed": total_passed, "checks": total_checks, "results": rows}, indent=2))
    print(f"Saved {out.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
