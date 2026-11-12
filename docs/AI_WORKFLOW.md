# How this was built with AI

**Tool:** Claude Code (CLI), model Claude Opus 5.5, in one session on 9 November 2026.
**Harness:** [`CLAUDE.md`](../CLAUDE.md) at the repo root. My personal Claude Code configuration, which covers unrelated projects, is not included.

## The prompts

**1. Opening prompt** (verbatim; local file paths removed):

> [attached: invoices_2025_clean.csv, procurement_policy.md, vendor_master.csv]
> Hello Claude, I have been asked to conduct this task to the best of my abilities, could you help me in the brainstorming and completing this task please? "Aim: AI-based Finance Spend Intelligence Assistant. [...my summary of the brief, including the four extras listed as required...]"

**2. Claude's first response** was a brainstorm, not code:
- It read the original brief PDF alongside my summary and flagged that the brief says *"pick one at most"* of the extras, where my summary listed all four as required.
- It profiled the data before designing anything: 3 currencies and no FX rates, 2 credit notes, an INACTIVE vendor in the Spain total, 21 invoices with no PO and 25 with no approver, and the questions the data cannot answer (approver roles, requesters).
- It proposed the core design (typed tools, the model never computes, a grounding validator) and asked me three decisions.

**3. My decisions** (answered through Claude Code's question prompt):

| Question | My answer |
|---|---|
| Which LLM path? | Anthropic API, with a labelled stub as fallback |
| Which single extra? | Eval set with a scoring script |
| When is the interview? | Within a week |

After that, Claude built it. The steps are below.

## Who decided what

[FILL: in your own words: which choices were yours, where you pushed back, what you would have done differently.]

Proposed by Claude and accepted by me:
- converting at ECB rates per invoice date instead of a fixed rate
- typed tools over text-to-SQL
- post-hoc grounding checks
- golden answers computed by an independent script

## How the output was verified

Checks that were run, not assumed:
- **Unit tests** on the deterministic layer (8 tests): FX on a weekend date, approval-band boundaries, totals that reconcile, an invented figure being caught.
- **Independent golden answers.** `eval/compute_golden.py` reimplements the calculations without importing app code. It matched the app on every figure.
- **The eval** run against the stub: 8/10 cases.
- **Rendered in a real browser** (headless Chrome, scripted to ask questions). This caught a bug the TypeScript build did not: in current Chrome `scrollIntoView` returns a Promise, which React treated as an effect clean-up and crashed the chat panel, leaving the page blank. It also caught a clipped summary column, raw `**` markdown in the policy quotes, and an unhelpful error message when the backend is down.

**Not verified at build time:** the LLM path itself. No API key was available in the session, so the Anthropic code has not run against the live API. [FILL: update once you have run `run_eval.py` with a key.]
