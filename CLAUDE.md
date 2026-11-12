# Spend Intelligence Assistant: project harness for Claude Code

Loaded automatically by Claude Code in this repo. These are the rules the AI worked under.

## Stack
- `backend/`: Python 3.12, FastAPI, Anthropic SDK. Run from `backend/` with `.venv/bin/...`.
- `frontend/`: React 19 + Vite + TypeScript. `npm run dev`; `/api` proxies to :8000.
- `eval/`: `cases.json`, `compute_golden.py` (independent of app code), `run_eval.py`.

## Invariants (do not break these)
1. **The model never computes figures.** Every number about invoices comes from a tool in `app/tools.py`. If a question needs a new kind of figure, add a tool or a filter, never a prompt instruction to calculate.
2. **Evidence is built from tool output and the policy file, never from model text.** `app/assistant.py` assembles it.
3. **Grounding is checked, not requested.** `app/grounding.py` runs on every answer. Failures surface as `unverified`; they are never hidden.
4. **No silent fallback.** If an API key is set and the API fails, return an error. The stub is used only when no key is configured, and it is labelled on every answer.
5. **Policy rules are pinned** to `RULES_POLICY_VERSION`. Changing the policy file means reviewing `tools.py` and re-running the eval.
6. **FX follows policy §5.1:** ECB rate on the invoice date, previous fixing on non-business days. Never a fixed rate.
7. `frontend/src/types.ts` mirrors `backend/app/schemas.py`. Change both together.

## Before saying something works
- `cd backend && .venv/bin/python -m pytest tests`
- `.venv/bin/python ../eval/run_eval.py`
- `cd frontend && npm run build` (typecheck)
- For UI changes, render it in a real browser. A typecheck does not catch runtime errors. (It missed one here: `scrollIntoView` returning a Promise inside `useEffect` blanked the page.)
- Say plainly what was not tested, e.g. the LLM path without an API key.

## Style
Prototype code: clear over clever, comments explain *why*, no speculative abstraction.
