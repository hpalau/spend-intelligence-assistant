import type { AskResponse, Source } from "../types";
import { Evidence } from "./Evidence";

const SOURCE_LABEL: Record<Source, string> = { invoice_data: "Invoice data", policy: "Procurement policy" };

const STATUS_LABEL: Record<AskResponse["status"], string> = {
  answered: "Answered",
  declined: "Cannot answer from available data",
  unverified: "Unverified: check before relying on this",
};

export function AnswerCard({ r }: { r: AskResponse }) {
  const sections = r.evidence.policy.map((p) => `§${p.section_id}`);
  const { observed, declared, consistent, rationale } = r.route;

  return (
    <div className={`answer answer-${r.status}`}>
      <div className="answer-head">
        <span className={`pill answer-pill-${r.status}`}>{STATUS_LABEL[r.status]}</span>
        <span className={`pill ${r.meta.mode === "stub" ? "stub" : ""}`}>
          {r.meta.mode === "stub" ? "STUB: rule-based, no LLM" : r.meta.model}
        </span>
      </div>

      {/* The route: what the answer is based on, shown before the answer itself. */}
      <div className="based-on">
        <strong>Based on: </strong>
        {observed.length === 0 && <span className="muted">no data or policy source</span>}
        {observed.map((s) => (
          <span key={s} className="pill source">
            {SOURCE_LABEL[s]}
            {s === "invoice_data" && ` · ${r.evidence.rows.length} rows`}
            {s === "policy" && sections.length > 0 && ` · ${sections.join(", ")}`}
          </span>
        ))}
        <div className="muted small">Why: {rationale}</div>
        {!consistent && (
          <div className="warn small">
            The assistant said it would use {declared.map((s) => SOURCE_LABEL[s]).join(" + ") || "nothing"}, but the system
            recorded {observed.map((s) => SOURCE_LABEL[s]).join(" + ") || "nothing"}. The evidence below is what actually ran.
          </div>
        )}
      </div>

      <div className="pre answer-text">{r.answer}</div>

      {r.missing_information && (
        <div className="missing"><strong>What's missing: </strong>{r.missing_information}</div>
      )}

      {!r.grounding.passed && (
        <div className="warn" role="alert">
          <strong>Could not trace parts of this answer to the evidence.</strong>
          {r.grounding.ungrounded_numbers.length > 0 && <div>Figures not found in the data or policy: {r.grounding.ungrounded_numbers.join(", ")}</div>}
          {r.grounding.unknown_invoices.length > 0 && <div>Invoices not returned by any query: {r.grounding.unknown_invoices.join(", ")}</div>}
          {r.grounding.unknown_sections.length > 0 && <div>Policy sections that do not exist: {r.grounding.unknown_sections.join(", ")}</div>}
        </div>
      )}

      {r.caveats.length > 0 && (
        <ul className="caveats">{r.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
      )}

      <Evidence evidence={r.evidence} />

      <div className="meta muted small">
        {r.meta.request_id} · prompt {r.meta.prompt_version} · policy v{r.meta.policy_version} · {r.meta.latency_ms} ms
        {r.meta.input_tokens != null && ` · ${r.meta.input_tokens} in / ${r.meta.output_tokens} out tokens`}
      </div>
    </div>
  );
}
