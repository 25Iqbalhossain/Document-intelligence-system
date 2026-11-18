import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, post, put } from "../api/client";
import type { Correction, Document, Finding, Invoice, SourceRef } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { DocumentViewer } from "../components/DocumentViewer";
import { Badge, Confidence, ErrorBox, Loading, PageHeader } from "../components/ui";
import { diffFields, formatDateTime, formatMoney, humanize } from "../utils/format";

const FIELDS: { key: string; label: string; kind?: "date" }[] = [
  { key: "invoice_number", label: "Invoice number" }, { key: "invoice_date", label: "Invoice date", kind: "date" },
  { key: "supplier_name", label: "Supplier" }, { key: "customer_name", label: "Customer" }, { key: "currency", label: "Currency" },
  { key: "subtotal", label: "Subtotal" }, { key: "tax", label: "Tax" }, { key: "total", label: "Total" },
];
const LINE_FIELDS = ["description", "quantity", "unit_price", "tax", "total"] as const;
type LineEdits = Record<number, Partial<Record<(typeof LINE_FIELDS)[number], string>>>;

const refOf = (inv: Invoice, field: string): SourceRef | undefined => inv.source_references.find((r) => r.field === field);
const where = (r?: SourceRef) => (r ? (r.cell ? `${r.sheet ?? ""}!${r.cell}` : r.page ? `page ${r.page}` : "") : "");

export default function InvoiceReview() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const reviewer = can("admin", "reviewer");

  const inv = useQuery({ queryKey: ["invoice", id], queryFn: () => api<Invoice>(`/invoices/${id}`) });
  const doc = useQuery({ queryKey: ["document", inv.data?.document_id], queryFn: () => api<Document>(`/documents/${inv.data!.document_id}`), enabled: !!inv.data });
  const audit = useQuery({ queryKey: ["corrections", id], queryFn: () => api<Correction[]>(`/invoices/${id}/corrections`) });

  const [edited, setEdited] = useState<Record<string, string>>({});
  const [lineEdits, setLineEdits] = useState<LineEdits>({});
  const [reason, setReason] = useState("");
  const [viewPage, setViewPage] = useState<number | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  const [note, setNote] = useState("");

  // Re-initialise the form whenever the server copy changes (new version after save / concurrent edit).
  useEffect(() => {
    if (!inv.data) return;
    setEdited(Object.fromEntries(FIELDS.map((f) => [f.key, (inv.data as unknown as Record<string, string | null>)[f.key] ?? ""])));
    setLineEdits({}); setReason("");
  }, [inv.data?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const original = useMemo(() => (inv.data ? Object.fromEntries(FIELDS.map((f) => [f.key, (inv.data as unknown as Record<string, string | null>)[f.key]])) : {}), [inv.data]);
  const changes = useMemo(() => diffFields(original as Record<string, string | null>, edited), [original, edited]);
  const lineChanges = useMemo(() => {
    const out: Record<number, Record<string, string | null>> = {};
    for (const li of inv.data?.line_items ?? []) {
      const e = lineEdits[li.line_no];
      if (!e) continue;
      const d = diffFields(li as unknown as Record<string, string | null>, Object.fromEntries(Object.entries(e).map(([k, v]) => [k, v ?? ""])));
      if (Object.keys(d).length) out[li.line_no] = d;
    }
    return out;
  }, [lineEdits, inv.data]);
  const dirty = Object.keys(changes).length + Object.keys(lineChanges).length > 0;

  const invalidate = () => { for (const k of ["invoice", "corrections", "doc-invoices", "review-queue", "stats", "document"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const save = useMutation({ mutationFn: () => put<Invoice>(`/invoices/${id}`, { version: inv.data!.version, fields: changes, lines: lineChanges, reason: reason.trim() || null }), onSuccess: (data) => { qc.setQueryData(["invoice", id], data); invalidate(); } });
  const decide = useMutation({ mutationFn: (decision: "approve" | "reject") => post<Invoice>(`/invoices/${id}/review`, { version: inv.data!.version, decision }), onSuccess: (data) => { qc.setQueryData(["invoice", id], data); invalidate(); } });
  const resolve = useMutation({ mutationFn: ({ fid, status }: { fid: string; status: "accepted" | "dismissed" }) => post<Finding>(`/findings/${fid}/resolve`, { status, note: note.trim() }), onSuccess: () => { setResolving(null); setNote(""); invalidate(); } });

  if (inv.isLoading) return <Loading />;
  if (inv.error || !inv.data) return <ErrorBox error={inv.error ?? new Error("Invoice not found")} />;
  const i = inv.data;
  const canJump = !!doc.data && (doc.data.mime_type === "application/pdf" || doc.data.mime_type.startsWith("image/"));
  const openErrors = i.findings.filter((f) => f.severity === "error" && f.resolution_status === "open");
  const order = { error: 0, warning: 1, info: 2 } as const;
  const findings = [...i.findings].sort((a, b) => order[a.severity] - order[b.severity] || (a.resolution_status === "open" ? -1 : 1));
  const conflict = [save.error, decide.error].some((e) => e instanceof Error && e.message.includes("changed"));

  return (
    <>
      <PageHeader title={`Invoice ${i.invoice_number ?? "(number missing)"}`}
        subtitle={`${i.supplier_name ?? "Unknown supplier"} · ${formatMoney(i.total, i.currency)}${i.row_ref ? ` · ${i.row_ref}` : ""}`}
        actions={<Link className="btn-secondary" to={`/documents/${i.document_id}`}>Back to document</Link>} />
      <div className="mb-4 flex items-center gap-2"><Badge value={i.review_status} /><span className="text-xs text-slate-500">version {i.version}</span></div>
      <ErrorBox error={save.error ?? decide.error ?? resolve.error} />
      {conflict && <button className="btn-secondary mb-4" onClick={() => void inv.refetch()}>Reload latest version</button>}

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="card order-2 h-[80vh] min-h-[400px] p-2 xl:order-1 xl:sticky xl:top-4" aria-label="Original document">
          {doc.data && <DocumentViewer documentId={i.document_id} mime={doc.data.mime_type} page={viewPage} />}
        </section>

        <div className="order-1 space-y-6 xl:order-2">
          <section className="card p-4" aria-label="Extracted fields">
            <h2 className="mb-3 text-sm font-semibold">Extracted fields</h2>
            <div className="space-y-3">
              {FIELDS.map((f) => {
                const c = i.confidence[f.key]; const ref = refOf(i, f.key); const orig = i.original_text[f.key] as string | undefined;
                const touched = f.key in changes;
                return (
                  <div key={f.key}>
                    <div className="mb-1 flex items-center justify-between gap-2"><label htmlFor={`f-${f.key}`} className="text-xs font-medium text-slate-600">{f.label}{touched && <span className="ml-1 text-brand-600">· edited</span>}</label><Confidence score={c?.score} status={c?.status} /></div>
                    <input id={`f-${f.key}`} className={`input ${touched ? "border-brand-500" : ""}`} type="text" inputMode={["subtotal", "tax", "total"].includes(f.key) ? "decimal" : undefined}
                      placeholder={f.kind === "date" ? "YYYY-MM-DD" : "not found in source"} pattern={f.kind === "date" ? "\\d{4}-\\d{2}-\\d{2}" : undefined}
                      disabled={!reviewer} value={edited[f.key] ?? ""} onChange={(e) => setEdited({ ...edited, [f.key]: e.target.value })} />
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-slate-500">
                      {ref?.text && <span className="max-w-full truncate" title={ref.text}>“{ref.text}”</span>}
                      {ref?.page && canJump && <button className="text-brand-600 hover:underline" onClick={() => setViewPage(ref.page!)}>show on page {ref.page}</button>}
                      {ref && (!ref.page || !canJump) && <span>{where(ref)}</span>}
                      {orig && orig !== i[f.key as keyof Invoice] && <span>original text: <span className="font-mono">{orig}</span></span>}
                      {c?.status && !["verified", "missing"].includes(c.status) && <span className="text-amber-700">{humanize(c.status)}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="card overflow-x-auto" aria-label="Line items">
            <h2 className="px-4 pt-4 text-sm font-semibold">Line items</h2>
            {i.line_items.length === 0 ? <p className="p-4 text-sm text-slate-500">No line items were extracted.</p> : (
              <table className="mt-2 w-full"><thead className="border-b border-slate-200"><tr><th className="th">#</th>{LINE_FIELDS.map((f) => <th key={f} className="th">{humanize(f)}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">{i.line_items.map((li) => (
                  <tr key={li.line_no}><td className="td tabular-nums">{li.line_no}</td>{LINE_FIELDS.map((f) => {
                    const cur = lineEdits[li.line_no]?.[f] ?? (li[f] ?? ""); const touched = lineEdits[li.line_no]?.[f] !== undefined && lineEdits[li.line_no]![f] !== (li[f] ?? "");
                    return <td key={f} className="td"><input aria-label={`Line ${li.line_no} ${f}`} className={`input min-w-[5rem] ${touched ? "border-brand-500" : ""}`} disabled={!reviewer} value={cur}
                      onChange={(e) => setLineEdits({ ...lineEdits, [li.line_no]: { ...lineEdits[li.line_no], [f]: e.target.value } })} /></td>;
                  })}</tr>))}</tbody></table>
            )}
          </section>

          {reviewer && (
            <section className="card space-y-3 p-4" aria-label="Decision">
              <label className="block text-xs font-medium text-slate-600">Reason for changes (kept in the audit trail)
                <input className="input mt-1" value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} placeholder="e.g. total misread on scan; confirmed against paper copy" />
              </label>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : `Save corrections${dirty ? ` (${Object.keys(changes).length + Object.keys(lineChanges).length})` : ""}`}</button>
                <button className="btn-secondary" disabled={!dirty} onClick={() => { setEdited(Object.fromEntries(FIELDS.map((f) => [f.key, (original as Record<string, string | null>)[f.key] ?? ""]))); setLineEdits({}); }}>Discard edits</button>
                <span className="grow" />
                <button className="btn-danger" disabled={dirty || decide.isPending} onClick={() => decide.mutate("reject")}>Reject</button>
                <button className="btn-primary bg-emerald-600 hover:bg-emerald-700" disabled={dirty || decide.isPending || openErrors.length > 0} onClick={() => decide.mutate("approve")}
                  title={openErrors.length ? "Resolve or correct every error finding first" : dirty ? "Save or discard your edits first" : undefined}>Approve</button>
              </div>
              {openErrors.length > 0 && <p className="text-xs text-amber-700">{openErrors.length} unresolved error finding(s) block approval. Correct the data, or accept/dismiss each finding with a reason.</p>}
            </section>
          )}

          <section className="card p-4" aria-label="Validation findings">
            <h2 className="mb-3 text-sm font-semibold">Validation findings</h2>
            {findings.length === 0 ? <p className="text-sm text-emerald-700">All deterministic checks passed.</p> : (
              <ul className="space-y-3">{findings.map((f) => (
                <li key={f.id} className={`rounded-md border p-3 text-sm ${f.resolution_status === "open" ? "border-slate-200" : "border-slate-100 bg-slate-50 opacity-75"}`}>
                  <div className="flex flex-wrap items-center gap-2"><Badge value={f.severity} /><span className="font-medium">{humanize(f.rule_id)}</span>{f.field_name && <span className="font-mono text-xs text-slate-500">{f.field_name}</span>}{f.resolution_status !== "open" && <Badge value={f.resolution_status} />}</div>
                  <p className="mt-1 text-slate-700">{f.description}</p>
                  {(f.expected_value || f.actual_value) && <p className="mt-1 font-mono text-xs text-slate-500">expected {f.expected_value ?? "—"} · found {f.actual_value ?? "—"}</p>}
                  {f.source_references.slice(0, 2).map((r, n) => <button key={n} className="mt-1 mr-3 text-xs text-brand-600 hover:underline" onClick={() => canJump && r.page && setViewPage(r.page)}>{r.text ? `“${r.text.slice(0, 60)}”` : "source"} {where(r)}</button>)}
                  {f.resolution_note && <p className="mt-1 text-xs italic text-slate-500">Note: {f.resolution_note}</p>}
                  {reviewer && f.resolution_status === "open" && f.severity !== "info" && (resolving === f.id ? (
                    <div className="mt-2 flex flex-wrap gap-2"><input autoFocus className="input max-w-xs" placeholder="Reason (required)" value={note} onChange={(e) => setNote(e.target.value)} />
                      <button className="btn-secondary" disabled={note.trim().length < 3 || resolve.isPending} onClick={() => resolve.mutate({ fid: f.id, status: "accepted" })}>Accept as is</button>
                      <button className="btn-secondary" disabled={note.trim().length < 3 || resolve.isPending} onClick={() => resolve.mutate({ fid: f.id, status: "dismissed" })}>Dismiss (false alarm)</button>
                      <button className="btn-secondary" onClick={() => { setResolving(null); setNote(""); }}>Cancel</button></div>
                  ) : <button className="btn-secondary mt-2" onClick={() => setResolving(f.id)}>Resolve…</button>)}
                </li>))}</ul>
            )}
          </section>

          <section className="card p-4" aria-label="Audit trail">
            <h2 className="mb-3 text-sm font-semibold">Correction history</h2>
            {!audit.data?.length ? <p className="text-sm text-slate-500">No manual corrections yet.</p> : (
              <ul className="space-y-2 text-sm">{audit.data.map((c, n) => <li key={n}><span className="font-medium">{humanize(c.field)}</span>: <span className="font-mono text-xs line-through">{c.old ?? "—"}</span> → <span className="font-mono text-xs">{c.new ?? "—"}</span>{c.reason && <span className="text-slate-500"> — {c.reason}</span>}<span className="ml-2 text-xs text-slate-400">{formatDateTime(c.at)}</span></li>)}</ul>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
