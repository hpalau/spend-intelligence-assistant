// Mirrors backend/app/schemas.py. Keep the two in step.

export type Source = "invoice_data" | "policy";

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

export interface InvoiceRow {
  invoice_number: string;
  invoice_date: string;
  vendor_id: string;
  vendor_name: string;
  vendor_country: string;
  vendor_status: string;
  description: string;
  amount: number;
  currency: string;
  amount_eur: number;
  fx_rate: number;
  fx_rate_date: string;
  cost_center: string;
  po_number: string | null;
  approved_by: string | null;
}

export interface AskResponse {
  status: "answered" | "declined" | "unverified";
  answer: string;
  missing_information: string | null;
  caveats: string[];
  route: { declared: Source[]; observed: Source[]; rationale: string; consistent: boolean };
  evidence: {
    policy: { section_id: string; heading: string; text: string; cited_by: "answer" | "tool" | "answer+tool" }[];
    tool_calls: {
      tool: string;
      source: Source | null;
      args: Record<string, unknown>;
      ok: boolean;
      error: string | null;
      result: Record<string, unknown> | null;
    }[];
    rows: InvoiceRow[];
    rows_truncated: boolean;
    fx_rates: { currency: string; rate_date: string; rate: number }[];
    fx_source: string | null;
  };
  grounding: { passed: boolean; ungrounded_numbers: string[]; unknown_invoices: string[]; unknown_sections: string[] };
  meta: {
    request_id: string;
    mode: "llm" | "stub";
    model: string | null;
    prompt_version: string;
    policy_version: string;
    latency_ms: number;
    input_tokens: number | null;
    output_tokens: number | null;
  };
}

export interface Summary {
  period: { from: string; to: string };
  invoice_count: number;
  vendor_count: number;
  total_eur: number;
  gross_eur: number;
  credit_notes_eur: number;
  by_currency: { currency: string; invoice_count: number; total_original: number; total_eur: number }[];
  by_vendor_status: { vendor_status: string; invoice_count: number; total_eur: number; vendors: string[] }[];
  controls: { key: string; label: string; invoice_count: number; total_eur: number; policy_section: string }[];
  fx_basis: string;
  policy: { doc_id: string; version: string };
  source_hashes: Record<string, string>;
  mode: "llm" | "stub";
}

export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
}
