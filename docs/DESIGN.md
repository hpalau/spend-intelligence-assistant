# Design: Spend Intelligence Assistant

The decisions behind the prototype, the options rejected and why, and what is built versus planned. Setup and the short write-up are in the [README](../README.md).

**Status labels used below:** **Built** (in the code and tested), **Partial** (some of it built), **Planned** (designed, not built).

---

## 1. The problem, and the principle

Finance analysts cross-reference invoices against the procurement policy: approvals, vendor status, spend thresholds. The assistant answers questions about both sources and shows exactly what each answer is based on.

**The assistant reads and reports. It never approves, pays or blocks anything; a person does.** This is enforced by design rather than by instruction: the four tools only read data and calculate, none writes anywhere, and nothing connects to a payment system. A model that misbehaved could not approve a payment, because no such action exists.

## 2. Three architectures considered

| | Architecture | Verdict | Why |
|---|---|---|---|
| A1 | **All-LLM.** Policy and invoice CSV pasted into the prompt; the model reads, calculates and answers. | Rejected | The model would do arithmetic over 140 rows in three currencies. Errors would be silent, figures would have no lineage to rows, and nothing could be checked. Fails the brief's core requirement: evidence. |
| A2 | **Multi-agent.** One LLM agent per step: fetch, check data, check version, answer, verify. | Rejected | Errors compound (five steps at 95% each give about 77% end to end). Cost and latency multiply. A model checking the same model makes correlated mistakes. Multi-agent pays off for open-ended, context-heavy, parallel work (e.g. reading 5,000 contracts), not 140 rows and a 50-line policy. |
| A3 | **Stages split between LLM and code.** The model handles language; code handles everything that can be checked. | **Chosen, partly built** | Each stage is the cheapest reliable way to do its job. Checks done by code are certain and independent of the model. Each stage can write its own audit record. |

**The test for each stage:** does it need language understanding? If yes, it goes to the LLM. Is it a check? If yes, it goes to code.

## 3. Architecture 3, stage by stage

| # | Stage | Done by | What it does | Failure it surfaces | Status |
|---|---|---|---|---|---|
| 0 | Intake | Code | Question length; history must alternate | 422 Bad request | **Built** |
| 1 | Source integrity | Code | Policy version pinned to the rule set; source-file fingerprints; ECB rate for every invoice date | Rules marked stale; "no rate for this date" | **Partial** (version pin and fingerprints built; not yet shown in the UI) |
| 2 | Plan | LLM | Choose sources and tool calls, ask a clarifying question, or decline | Wrong route (declared vs actual sources compared) | **Built:** answer, clarify, or decline |
| 3 | Execute | Code | Run the tools; validate every argument | Invalid arguments returned to the model as errors | **Built** |
| 4 | Compose | LLM | Answer in a fixed JSON format: status, answer, sources, clauses, caveats, what's missing | Malformed output becomes an error | **Built**; **Planned:** structured claims |
| 5 | Verify | Code | Every number, invoice ID and clause in the answer must exist in the evidence | Status "Unverified", naming the untraced item | **Built** (number-level); **Planned:** claim-level |
| 6 | Present and audit | Code | Evidence assembled from tool output; one audit record per answer | n/a | **Built**; **Planned:** one record per stage |

### The four tools (`backend/app/tools.py`)

| Tool | Answers | Source |
|---|---|---|
| `spend_summary` | Totals, rankings, breakdowns (by vendor, country, status, currency, cost center, month) | Invoice data |
| `find_invoices` | Lists of invoices matching filters (missing PO, missing approver, status, amount) | Invoice data |
| `check_invoice` | One invoice against policy rules: pass / fail / cannot_verify per rule | Invoice data + policy rules |
| `approval_requirement` | Required approver for an amount, converting non-EUR at the ECB rate for the date | Policy rules |

Typed tools were chosen over text-to-SQL. They are less flexible, but every argument is validated, every figure carries the rows behind it, and a question the tools cannot express is declined rather than improvised.

## 4. What the data forced

- **Currency.** EUR, GBP and USD, with no exchange rates in the pack. Policy §5.1 requires the ECB reference rate on the invoice date. The repo includes a snapshot of the ECB's published history; each invoice is converted at its own date (the previous fixing on weekends and holidays), and every row shows its rate and rate date. Example: **a GBP 45,000 invoice dated 2 June 2025 is EUR 53,355.47, which needs a Finance Director, not a Department Head.**
- **The #2 vendor by spend, PharmaPrint Studios (EUR 1.08M), is INACTIVE**, and it is 41% of Spain spend. Answers flag it under §4.1.
- **Two credit notes** (negative amounts). Totals are net of credit notes by default, and answers say so. Matching them to originals (§6.1) cannot be checked: there is no link field.
- **What the data cannot answer:** approver roles (`approved_by` is a username), requesters (so §7 segregation of duties), payment status, any year but 2025. Rule checks return `cannot_verify` for these, and the assistant declines rather than infers.
- **Rules are pinned to policy v3.2.** If the policy file's version changes, the checks report themselves stale instead of running.

## 5. Context, retrieval and vector databases

- **The whole policy is in context** (about 1,500 tokens) with a `[§id]` marker on each clause. The model cannot miss a clause through a failed search, and it can reason across clauses (e.g. §9.1 emergency purchases override §2.1).
- **References without retrieval:** the model cites clause IDs; the backend serves the clause text from the source file. The text shown is never quoted by the model.
- **No vector database.**
  - For a 50-line policy it adds a silent failure mode (a missed passage gives no warning) and breaks the §3 approval table when the text is split into chunks.
  - For invoices it is the wrong tool entirely: exact figures belong in queries.
  - It would earn its place with hundreds of policies across versions and jurisdictions. Even then: filter by document, version, jurisdiction and effective date first, and keep returning clause IDs.
- **Anthropic's native citations** cannot be combined with the fixed JSON answer format. That trade-off was accepted in favour of the fixed format plus our own clause verification.

## 6. Hallucinations: prevent, detect, show, measure

**In this system, a hallucination can be:**
- a made-up figure;
- a real figure attached to the wrong entity;
- a wrong or made-up citation;
- a policy requirement the text doesn't contain;
- an answer to a question the data can't answer;
- general knowledge from the model's training used in place of this policy.

1. **Prevent.**
   - Tools do every calculation.
   - The data description tells the model what the data does *not* contain.
   - Declining is a normal output, with a "what's missing" field. Rules in a prompt alone are not enough.
2. **Detect.** The grounding check runs on every answer. **Known gap:** it proves a number exists in the evidence, not that it belongs to the right vendor; swapped totals would pass. **Planned fix:** structured claims (section 8).
3. **Show.**
   - "Unverified" status, naming the figure that could not be traced.
   - "Cannot answer from available data", with what's missing.
   - A warning when the declared route and the actual route differ.
4. **Measure.** The eval (section 7).

**Failure is never hidden.** If an API key is configured and the API fails, the user sees an error. The app never falls back to the stub silently.

## 7. Evaluation

**Built:** `eval/cases.json` (10 cases) and `eval/run_eval.py`.
- Golden answers come from `eval/compute_golden.py`, which shares no code with the app. Golden values taken from the app's own tools would make the eval circular.
- Each case is scored on: status, route, figures, required phrases, forbidden phrases, citations and grounding.
- The cases cover:
  - the three brief questions;
  - a currency trap;
  - a compliance check;
  - questions the data cannot answer (approver authority, 2024);
  - an ambiguous question that must be clarified (an invoice not identified);
  - a prompt injection.

**Results:** the stub passes 9/10 (it fails the injection case). That proves the scoring harness works, not quality: the stub's keyword rules were written for these questions. The LLM run is reported in the README.

### Proposed rubric

| Dimension | Weight | Scored by | Pass bar |
|---|---|---|---|
| Factual correctness | Critical | Code, against golden answers (to the cent, or 0.5%) | All figures correct |
| Faithfulness | Critical | Code: every claim traces to tool output | All claims verified |
| Abstention | Critical | Code: answer / clarify / decline matches expected | Correct outcome |
| Injection resistance | Critical | Code: forbidden phrases absent | Pass |
| Routing | High | Code: required sources used, forbidden not | Match |
| Citations | High | Code: required clauses cited (precision and recall) | All required present |
| Caveats | Medium | Code (required phrases) or grader | Relevant flag present |
| Clarity and usefulness | Low | LLM grader, 1–4 scale with anchor examples | Mean ≥ 3 |

**Gating rule:** any critical failure fails the case, however good the prose.

### Statistics

| What | Method |
|---|---|
| Pass rates | A proportion with a **Wilson 95% confidence interval**. With 10 cases, 8/10 has an interval of about 49–94%, so 10 cases is a smoke test. A pilot needs 50–100 cases across categories. |
| Comparing two prompts or models on the same cases | **McNemar's test** (paired pass/fail). |
| Decline decisions | Precision and recall from a confusion matrix: answered-when-it-should-decline, and declined-when-it-could-answer. |
| Citations | Precision and recall against the required clauses. |
| Non-determinism | Run each case 5 times; report the share that pass every time, not just once. |
| LLM grader for prose quality | **Cohen's kappa** against two human finance reviewers. Trust the grader only at κ ≥ 0.6–0.7. |
| Hallucination, the strongest test | **Counterfactual inputs:** a copy of the data with PharmaPrint set to ACTIVE, and the policy's 50,000 threshold changed to 40,000. A grounded system follows the changed inputs; one answering from training knowledge does not. |

## 8. Structured claims (planned)

The final answer will carry every factual claim with a pointer to its source:

```json
"claims": [
  {"subject": "PharmaPrint Studios (V-1006)", "metric": "net_spend_eur", "value": 1084833.66,
   "source": {"call": 0, "path": "groups[1]"}},
  {"subject": "PharmaPrint Studios (V-1006)", "metric": "vendor_status", "value": "INACTIVE"},
  {"subject": "approval_band", "metric": "required_approver", "value": "Finance Director",
   "source": {"policy": "3"}}
]
```

Code then checks each claim:
- the referenced tool result exists;
- its entity matches the subject;
- the value matches to the cent;
- statuses come from the fixed list and match the vendor master;
- policy claims match the clause text.

Any number in the answer that isn't backed by a claim is rejected. A swapped vendor then fails, naming the claim. The UI can link each figure in the answer to the row it came from.

## 9. API contract and frontend

- `GET /summary`:
  - net, gross and credit-note totals;
  - by currency (original and EUR);
  - by vendor status;
  - policy flag counts;
  - FX basis, policy version, source fingerprints.
- `POST /ask {question, history[]}` returns:
  - `status`: answered, clarify, declined or unverified;
  - `answer`, `missing_information`, `caveats`;
  - `route`: declared, observed, rationale, consistent;
  - `evidence`: policy clauses, tool calls, invoice rows, FX rates;
  - `grounding`;
  - `meta`: mode, model, prompt version, policy version, latency, tokens.
- Errors return `{code, message, retryable}` with 429, 502 or 503; validation errors return 422.
- The frontend:
  - `types.ts` mirrors the contract.
  - Summary states: loading, error with retry, ready.
  - Chat states: each exchange is pending, done or error, in one reducer.
  - Retry is offered when the server marks an error retryable.
  - **"Based on" is shown before the answer.**
  - The evidence panel shows policy text, invoice rows, the queries that ran, and exchange rates.

## 10. Production (200 users, real data)

| Area | What's needed |
|---|---|
| **Data** | ERP/accounts-payable warehouse as the source, tools as parameterised queries over versioned snapshots, a daily ECB feed, and a user and role directory (turns most `cannot_verify` checks into real checks). |
| **Access control** | SSO plus row-level security by entity and cost center, enforced in the tool layer so the model never receives rows the user cannot see. |
| **Audit (§8.2)** | The lineage record per answer already exists. It moves to an append-only store with user identity, kept for 10 years (§8.1) where outputs feed reporting, plus one record per stage. |
| **Policies as code** | Versions and effective dates per jurisdiction. A new version marks rules stale and re-runs the eval. |
| **Changes** | Improvements go through prompt or code changes that must pass the eval first. No automatic self-improvement in a financial-controls setting. |
| **Cost (estimate, not measured)** | About $0.06 per question on Claude Opus 5.5; about $4k a month at 200 users × 15 questions a day. Reduced by prompt caching and a smaller model for simple questions. |
| **Locale** | Parse "60.000" as sixty thousand where the user's locale says so; show the currency on every figure. |
| **Operations** | EU-region inference, rate limits, streaming, and monitoring of grounding failures, declines and route mismatches. A weekly sample is reviewed by a Finance controller and becomes new eval cases. |

## 11. Left out, and next

- **Left out:**
  - chat persistence (the browser holds the conversation and sends the last 3 turns);
  - auth, deployment and CI;
  - streaming;
  - a vector database;
  - text-to-SQL;
  - visual polish.
- **Next, in order:**
  1. Run and fix the LLM eval.
  2. Structured claims, with figures in the answer linked to their rows.
  3. One audit record per stage.
  4. Source integrity shown in the UI.
  5. Counterfactual eval cases.
  6. Grow the eval to 50+ cases, with confidence intervals.
