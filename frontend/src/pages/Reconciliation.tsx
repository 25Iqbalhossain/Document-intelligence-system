import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, downloadBlob, post } from "../api/client";
import type { Document, MatchRow, Report } from "../api/types";
import { Badge, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { formatDateTime, humanize } from "../utils/format";

function DocPicker({ title, docs, selected, onChange, disabled }: { title: string; docs: Document[]; selected: string[]; onChange: (ids: string[]) => void; disabled: Set<string> }) {
  return (
    <fieldset className="card p-4"><legend className="px-1 text-sm font-semibold">{title}</legend>
      {docs.length === 0 ? <p className="text-sm text-slate-500">No processed documents available.</p> : (
        <ul className="max-h-64 space-y-1 overflow-auto">{docs.map((d) => (
          <li key={d.id}><label className={`flex items-center gap-2 text-sm ${disabled.has(d.id) ? "opacity-40" : ""}`}>
            <input type="checkbox" disabled={disabled.has(d.id)} checked={selected.includes(d.id)} onChange={(e) => onChange(e.target.checked ? [...selected, d.id] : selected.filter((x) => x !== d.id))} />
            <span className="truncate">{d.original_filename}</span><span className="text-xs text-slate-400">{humanize(d.doc_type)} · {formatDateTime(d.created_at)}</span></label></li>))}</ul>)}
    </fieldset>
  );
}

const outcomeNote: Record<MatchRow["outcome"], string> = {
  reconciled: "Match confirmed, no differences", needs_confirmation: "Identical, but the match itself needs confirming",
  discrepancies: "Differences found", rejected: "Match rejected by a reviewer",
};

export default function Reconciliation() {
  const qc = useQueryClient();
  const docs = useQuery({ queryKey: ["documents", "reconcilable"], queryFn: async () => (await api<Document[]>("/documents?limit=200")).filter((d) => ["processed", "needs_review"].includes(d.status)) });
  const [left, setLeft] = useState<string[]>([]); const [right, setRight] = useState<string[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const run = useMutation({ mutationFn: () => post<Report>("/reconciliation", { left_document_ids: left, right_document_ids: right }), onSuccess: setReport });
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "confirm" | "reject" }) => post(`/relationships/${id}/${action}`),
    onSuccess: async () => { if (report) setReport(await api<Report>(`/reconciliation/${report.run_id}`)); void qc.invalidateQueries({ queryKey: ["reconciliation"] }); },
  });
  if (docs.isLoading) return <Loading />;
  const s = report?.summary;
  return (
    <>
      <PageHeader title="Reconciliation" subtitle="Cross-check two sets of documents (PDF ↔ PDF, PDF ↔ Excel/CSV). Only exact matches are confirmed automatically; everything else waits for you." />
      <ErrorBox error={docs.error ?? run.error ?? decide.error} />
      <div className="grid gap-4 md:grid-cols-2">
        <DocPicker title="Side A (e.g. invoices)" docs={docs.data ?? []} selected={left} onChange={setLeft} disabled={new Set(right)} />
        <DocPicker title="Side B (e.g. ledger)" docs={docs.data ?? []} selected={right} onChange={setRight} disabled={new Set(left)} />
      </div>
      <div className="mt-4"><button className="btn-primary" disabled={!left.length || !right.length || run.isPending} onClick={() => run.mutate()}>{run.isPending ? "Reconciling…" : "Run reconciliation"}</button></div>

      {report && s && (
        <section className="mt-8" aria-label="Report">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold">Result</h2>
            <button className="btn-secondary" onClick={() => void downloadBlob(`/reconciliation/${report.run_id}/csv`, `reconciliation-${report.run_id}.csv`)}>Download CSV</button></div>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5 text-sm">
            {[["Side A records", s.left_count], ["Side B records", s.right_count], ["Matched", s.matched], ["Unmatched A", s.unmatched_left.length], ["Unmatched B", s.unmatched_right.length]].map(([k, v]) => <div key={k as string} className="card p-3"><div className="text-xs text-slate-500">{k}</div><div className="text-xl font-semibold tabular-nums">{v}</div></div>)}
          </div>
          {(s.duplicates_left.length > 0 || s.duplicates_right.length > 0) && <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Duplicate invoice numbers found within one side ({s.duplicates_left.length + s.duplicates_right.length} group(s)). They are flagged, never deleted or merged.</div>}
          {s.ambiguous.length > 0 && <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{s.ambiguous.length} record group(s) had several possible matches and were left for manual review: {s.ambiguous.map((a) => String(a.reason)).map(humanize).join(", ")}.</div>}

          <h3 className="mb-2 text-sm font-semibold">Matches</h3>
          {report.matches.length === 0 ? <Empty>No records could be matched.</Empty> : (
            <ul className="space-y-3">{report.matches.map((m) => (
              <li key={m.id} className="card p-4">
                <div className="flex flex-wrap items-center gap-2"><Badge value={m.outcome} label={outcomeNote[m.outcome]} /><Badge value={m.status} /><span className="text-xs text-slate-500">method: {humanize(m.method)}{m.score != null && ` · score ${m.score}`}</span>
                  <span className="grow" /><Link className="text-xs text-brand-600 hover:underline" to={`/invoices/${m.left_invoice_id}`}>A</Link><Link className="text-xs text-brand-600 hover:underline" to={`/invoices/${m.right_invoice_id}`}>B</Link></div>
                {m.discrepancies.length > 0 && <table className="mt-3 w-full text-sm"><thead><tr><th className="th">Field</th><th className="th">A</th><th className="th">B</th><th className="th">Issue</th></tr></thead>
                  <tbody className="divide-y divide-slate-100">{m.discrepancies.map((d, n) => <tr key={n}><td className="td">{humanize(d.field)}</td><td className="td font-mono text-xs">{d.left ?? "—"}</td><td className="td font-mono text-xs">{d.right ?? "—"}</td><td className="td"><Badge value={d.severity} /> <span className="text-slate-600">{d.description}</span></td></tr>)}</tbody></table>}
                {m.status === "proposed" && <div className="mt-3 flex gap-2"><button className="btn-primary" disabled={decide.isPending} onClick={() => decide.mutate({ id: m.id, action: "confirm" })}>Confirm match</button><button className="btn-secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: m.id, action: "reject" })}>Reject match</button></div>}
              </li>))}</ul>
          )}
          {(s.unmatched_left.length > 0 || s.unmatched_right.length > 0) && (
            <div className="mt-6 grid gap-4 md:grid-cols-2">{([["Only in A", s.unmatched_left], ["Only in B", s.unmatched_right]] as const).map(([t, rows]) => (
              <div key={t} className="card p-4"><h3 className="mb-2 text-sm font-semibold">{t} ({rows.length})</h3><ul className="max-h-64 space-y-1 overflow-auto text-sm text-slate-700">{rows.map((r) => <li key={r.id}><Link className="text-brand-600 hover:underline" to={`/invoices/${r.id}`}>{r.label}</Link></li>)}</ul></div>))}</div>
          )}
        </section>
      )}
    </>
  );
}
