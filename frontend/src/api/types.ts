export type Role = "admin" | "reviewer" | "uploader" | "viewer";
export interface User { id: string; email: string; full_name: string | null; role: Role; is_active: boolean }
export interface Document {
  id: string; original_filename: string; mime_type: string; size_bytes: number; page_count: number | null;
  doc_type: string; status: string; error: string | null; created_at: string;
}
export interface UploadResult { document: Document; job_id: string; duplicate_of: string[] }
export interface Job {
  id: string; parent_job_id: string | null; stage: string; status: string; attempts: number; max_attempts: number;
  page_start: number | null; page_end: number | null; error: string | null;
}
export interface Progress {
  status: string | null; pages_total: number | null; pages_done: number; pages_failed: number; percent: number | null;
  jobs: Record<string, number>;
}
export interface HistoryEvent { at: string; event: string; actor: string; job_id: string | null; detail: Record<string, unknown> }
export interface PageInfo { page: number; kind: string; label: string | null; status: string; ocr_confidence: number | null; text_chars: number; error: string | null }
export interface SourceRef { field?: string; page?: number; text?: string; sheet?: string; row?: number; cell?: string; match?: string }
export interface FieldConfidence { score: number | null; status: string; [k: string]: unknown }
export interface Line {
  line_no: number; description: string | null; quantity: string | null; unit_price: string | null; tax: string | null;
  total: string | null; confidence: Record<string, FieldConfidence>; source_references: SourceRef[];
}
export interface Finding {
  id: string; rule_id: string; field_name: string | null; severity: "info" | "warning" | "error";
  expected_value: string | null; actual_value: string | null; description: string; source_references: SourceRef[];
  resolution_status: "open" | "accepted" | "corrected" | "dismissed"; resolution_note: string | null;
}
export interface Invoice {
  id: string; document_id: string; row_ref: string | null; invoice_number: string | null; invoice_date: string | null;
  supplier_name: string | null; customer_name: string | null; subtotal: string | null; tax: string | null;
  total: string | null; currency: string | null; confidence: Record<string, FieldConfidence>;
  source_references: SourceRef[]; original_text: Record<string, unknown>; requires_review: boolean;
  review_status: "not_required" | "pending" | "approved" | "rejected"; version: number;
  line_items: Line[]; findings: Finding[];
}
export interface Correction { at: string; field: string; old: string | null; new: string | null; reason: string | null; user_id: string }
export interface QueueItem {
  invoice_id: string; document_id: string; invoice_number: string | null; supplier_name: string | null;
  total: string | null; open_errors: number; open_warnings: number;
}
export interface Stats { documents: number; by_status: Record<string, number>; invoices_pending_review: number; failed_jobs: number }
export interface SearchHit {
  document_id: string; filename: string; page: number | null; snippet: string; score: number;
  ranks: Record<string, number>; raw_scores: Record<string, number>;
}
export interface InvoiceHit {
  invoice_id: string; document_id: string; filename: string; invoice_number: string | null;
  supplier_name: string | null; total: string | null; currency: string | null; review_status: string;
}
export interface SearchResponse {
  query: string; mode: string; embedding_model: string; semantic: boolean; results: SearchHit[]; invoices: InvoiceHit[];
}
export interface Discrepancy {
  field: string; kind: string; severity: "info" | "warning" | "error"; left: string | null; right: string | null;
  description: string;
}
export interface MatchRow {
  id: string; left_invoice_id: string; right_invoice_id: string; method: string; score: number | null; status: string;
  errors: number; warnings: number; discrepancies: Discrepancy[];
  outcome: "reconciled" | "needs_confirmation" | "discrepancies" | "rejected";
}
export interface Unmatched { id: string; document_id: string; label: string }
export interface Report {
  run_id: string; created_at: string;
  summary: {
    left_count: number; right_count: number; matched: number; confirmed: number; proposed: number;
    unmatched_left: Unmatched[]; unmatched_right: Unmatched[];
    duplicates_left: Unmatched[][]; duplicates_right: Unmatched[][]; ambiguous: Record<string, unknown>[];
  };
  matches: MatchRow[];
}
