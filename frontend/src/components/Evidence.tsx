import { fmtAmount, fmtEur } from "../format";
import type { AskResponse } from "../types";

type Ev = AskResponse["evidence"];

// Everything here is built by the backend from tool output and the policy file.
// The model's text is never rendered as evidence.
export function Evidence({ evidence }: { evidence: Ev }) {
  const { policy, rows, tool_calls, fx_rates } = evidence;
  if (!policy.length && !rows.length && !tool_calls.length) {
    return <p className="muted small">No evidence was retrieved for this answer.</p>;
  }
  return (
    <div className="evidence">
      {policy.length > 0 && (
        <details open>
          <summary>Policy text ({policy.length} {policy.length === 1 ? "clause" : "clauses"})</summary>
          {policy.map((p) => (
            <blockquote key={p.section_id}>
              <div className="quote-head">
                §{p.section_id} · {p.heading}
                <span className="muted small"> · {p.cited_by === "tool" ? "applied by a rule check" : "cited in the answer"}</span>
              </div>
              <div className="pre">{p.text}</div>
            </blockquote>
          ))}
        </details>
      )}

      {rows.length > 0 && (
        <details>
          <summary>Invoice rows ({rows.length}{evidence.rows_truncated ? ", truncated" : ""})</summary>
          <div className="table-scroll">
            <table className="rows">
              <thead>
                <tr>
                  <th>Invoice</th><th>Date</th><th>Vendor</th><th>Status</th>
                  <th className="num">Amount</th><th className="num">EUR equiv.</th><th>PO</th><th>Approver</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.invoice_number}>
                    <td>{r.invoice_number}</td>
                    <td>{r.invoice_date}</td>
                    <td>{r.vendor_name} <span className="muted">({r.vendor_country})</span></td>
                    <td><span className={`pill status-${r.vendor_status}`}>{r.vendor_status}</span></td>
                    <td className="num">{fmtAmount(r.amount, r.currency)}</td>
                    <td className="num" title={r.currency === "EUR" ? "" : `ECB ${r.fx_rate} ${r.currency}/EUR on ${r.fx_rate_date}`}>
                      {fmtEur(r.amount_eur)}
                    </td>
                    <td className={r.po_number ? "" : "flag"}>{r.po_number ?? "missing"}</td>
                    <td className={r.approved_by ? "" : "flag"}>{r.approved_by ?? "missing"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {tool_calls.length > 0 && (
        <details>
          <summary>How it was calculated ({tool_calls.length} {tool_calls.length === 1 ? "query" : "queries"})</summary>
          {tool_calls.map((c, i) => (
            <div key={i} className="tool-call">
              <div>
                <code>{c.tool}</code> <span className="muted small">→ {c.source === "policy" ? "policy rules" : "invoice data"}</span>
                {!c.ok && <span className="pill bad"> failed</span>}
              </div>
              <pre>{JSON.stringify(c.args, null, 2)}</pre>
              {c.error && <p className="flag small">{c.error}</p>}
              {c.result && <ResultPreview result={c.result} />}
            </div>
          ))}
        </details>
      )}

      {fx_rates.length > 0 && (
        <details>
          <summary>Exchange rates used ({fx_rates.length})</summary>
          <p className="muted small">{evidence.fx_source}. Weekend and holiday invoices use the previous fixing.</p>
          <ul className="small">
            {fx_rates.map((f) => (
              <li key={`${f.currency}-${f.rate_date}`}>{f.rate_date}: 1 EUR = {f.rate} {f.currency}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// Shows the scalar fields and grouped totals of a tool result; the rows themselves
// are already in the table above.
function ResultPreview({ result }: { result: Record<string, unknown> }) {
  const groups = result.groups as { key: string; total_eur: number; invoice_count: number }[] | undefined;
  const checks = result.checks as { rule: string; policy_section: string; status: string; detail: string }[] | undefined;
  const scalars = Object.entries(result).filter(([, v]) => typeof v !== "object" || v === null);
  return (
    <div className="small">
      {scalars.length > 0 && (
        <ul>{scalars.map(([k, v]) => <li key={k}><span className="muted">{k}:</span> {String(v)}</li>)}</ul>
      )}
      {groups && groups.length > 0 && (
        <table>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key}><td>{g.key}</td><td className="num">{fmtEur(g.total_eur)}</td><td className="num">{g.invoice_count} inv.</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {checks && (
        <table>
          <tbody>
            {checks.map((c) => (
              <tr key={c.rule}>
                <td>{c.rule} <span className="muted">§{c.policy_section}</span></td>
                <td><span className={`pill check-${c.status}`}>{c.status.replace("_", " ")}</span></td>
                <td>{c.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
