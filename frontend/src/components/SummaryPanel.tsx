import { useCallback, useEffect, useState } from "react";
import { getSummary, RequestFailed } from "../api";
import { fmtAmount, fmtEur } from "../format";
import type { Summary } from "../types";

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: Summary }
  | { kind: "error"; message: string };

export function SummaryPanel() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    setState({ kind: "loading" });
    getSummary()
      .then((data) => setState({ kind: "ready", data }))
      .catch((e: RequestFailed) => setState({ kind: "error", message: e.message }));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <aside className="summary" aria-busy="true">
        <h2>Invoice overview</h2>
        <p className="muted">Loading summary…</p>
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton short" />
      </aside>
    );
  }
  if (state.kind === "error") {
    return (
      <aside className="summary">
        <h2>Invoice overview</h2>
        <div className="error-box" role="alert">
          <p>Summary unavailable: {state.message}</p>
          <button onClick={load}>Try again</button>
        </div>
      </aside>
    );
  }

  const s = state.data;
  return (
    <aside className="summary">
      <h2>Invoice overview</h2>
      <p className="muted">
        {s.invoice_count} invoices · {s.vendor_count} vendors · {s.period.from} to {s.period.to}
      </p>

      <div className="kpi">
        <div className="kpi-value">{fmtEur(s.total_eur)}</div>
        <div className="muted">
          Net spend, EUR-equivalent. Gross {fmtEur(s.gross_eur)}, credit notes {fmtEur(s.credit_notes_eur)}.
        </div>
      </div>

      <h3>By currency</h3>
      <table>
        <thead>
          <tr><th>Currency</th><th className="num">Invoices</th><th className="num">Original</th><th className="num">EUR equiv.</th></tr>
        </thead>
        <tbody>
          {s.by_currency.map((c) => (
            <tr key={c.currency}>
              <td>{c.currency}</td>
              <td className="num">{c.invoice_count}</td>
              <td className="num">{fmtAmount(c.total_original, c.currency)}</td>
              <td className="num">{fmtEur(c.total_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>By vendor status</h3>
      <table>
        <thead>
          <tr><th>Status</th><th className="num">Invoices</th><th className="num">EUR equiv.</th></tr>
        </thead>
        <tbody>
          {s.by_vendor_status.map((v) => (
            <tr key={v.vendor_status} title={v.vendors.join(", ")}>
              <td><span className={`pill status-${v.vendor_status}`}>{v.vendor_status}</span></td>
              <td className="num">{v.invoice_count}</td>
              <td className="num">{fmtEur(v.total_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Not ACTIVE: {s.by_vendor_status.filter((v) => v.vendor_status !== "ACTIVE").flatMap((v) => v.vendors.map((n) => `${n} (${v.vendor_status})`)).join(", ")}
      </p>

      <h3>Policy flags</h3>
      <ul className="controls">
        {s.controls.map((c) => (
          <li key={c.key}>
            <span>{c.label} <span className="muted">§{c.policy_section}</span></span>
            <span className="num">{c.invoice_count} · {fmtEur(c.total_eur)}</span>
          </li>
        ))}
      </ul>

      <p className="muted small">{s.fx_basis}</p>
      <p className="muted small">Policy {s.policy.doc_id} v{s.policy.version}.</p>
    </aside>
  );
}
