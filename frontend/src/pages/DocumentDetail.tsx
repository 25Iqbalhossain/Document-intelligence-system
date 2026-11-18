import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, downloadBlob, post } from "../api/client";
import type { Document, HistoryEvent, Invoice, Job, PageInfo, Progress } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { DocumentViewer } from "../components/DocumentViewer";
import { Badge, Confidence, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { formatBytes, formatDateTime, formatMoney, humanize } from "../utils/format";

const ACTIVE = ["uploaded", "extracting_text", "ai_extracting", "validating_financials"];

export default function DocumentDetail() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"invoices" | "pages" | "jobs" | "history" | "file">("invoices");
  const [textPage, setTextPage] = useState<number | null>(null);

  const doc = useQuery({ queryKey: ["document", id], queryFn: () => api<Document>(`/documents/${id}`),
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 2_000 : false) });
  const active = !!doc.data && ACTIVE.includes(doc.data.status);
  const progress = useQuery({ queryKey: ["progress", id], queryFn: () => api<Progress>(`/documents/${id}/progress`), refetchInterval: active ? 2_000 : false });
  const invoices = useQuery({ queryKey: ["doc-invoices", id, doc.data?.status], queryFn: () => api<Invoice[]>(`/documents/${id}/invoices`), enabled: !!doc.data });
  const pages = useQuery({ queryKey: ["pages", id, doc.data?.status], queryFn: () => api<PageInfo[]>(`/documents/${id}/pages`), enabled: tab === "pages" });
  const jobs = useQuery({ queryKey: ["jobs", id, doc.data?.status], queryFn: () => api<Job[]>(`/documents/${id}/jobs`), enabled: tab === "jobs" || tab === "history" || !!doc.data?.error });
  const history = useQuery({ queryKey: ["history", id, doc.data?.status], queryFn: () => api<HistoryEvent[]>(`/documents/${id}/history`), enabled: tab === "history" });
  const pageText = useQuery({ queryKey: ["page-text", id, textPage], queryFn: () => api<{ text: string; source: string; ocr_confidence: number | null }>(`/documents/${id}/pages/${textPage}/text`), enabled: textPage !== null });

  const refresh = () => { for (const k of ["document", "progress", "jobs", "history", "stats"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const act = useMutation({ mutationFn: ({ job, action }: { job: string; action: "retry" | "cancel" }) => post(`/jobs/${job}/${action}`), onSuccess: refresh });
  const del = useMutation({ mutationFn: () => api(`/documents/${id}`, { method: "DELETE" }), onSuccess: () => { window.location.assign("/documents"); } });

  if (doc.isLoading) return <Loading />;
  if (doc.error || !doc.data) return <ErrorBox error={doc.error ?? new Error("Document not found")} />;
  const d = doc.data;
  const root = jobs.data?.find((j) => j.parent_job_id === null && j.stage === "extract_text");
  const failedJobs = jobs.data?.filter((j) => ["failed", "dead"].includes(j.status)) ?? [];
  const canWrite = can("admin", "reviewer", "uploader");
  const tabs = [["invoices", `Invoices${invoices.data ? ` (${invoices.data.length})` : ""}`], ["pages", "Pages"], ["jobs", "Jobs"], ["history", "History"], ["file", "Original file"]] as const;

  return (
    <>
      <PageHeader title={d.original_filename} subtitle={`${humanize(d.doc_type)} · ${formatBytes(d.size_bytes)}${d.page_count ? ` · ${d.page_count} page(s)` : ""} · uploaded ${formatDateTime(d.created_at)}`}
        actions={<>
          <button className="btn-secondary" onClick={() => void downloadBlob(`/documents/${id}/file`, d.original_filename)}>Download</button>
          {can("admin") && <button className="btn-danger" onClick={() => confirm("Delete this document? It is hidden from all users; processing history is kept.") && del.mutate()}>Delete</button>}
        </>} />
      <div className="mb-4 flex flex-wrap items-center gap-3"><Badge value={d.status} />
        {progress.data?.percent != null && active && <span className="text-sm text-slate-600">{progress.data.pages_done}/{progress.data.pages_total} pages ({progress.data.percent}%)</span>}
        {(progress.data?.pages_failed ?? 0) > 0 && <span className="text-sm text-red-700">{progress.data?.pages_failed} page(s) failed</span>}
      </div>
      {active && progress.data?.percent != null && <div className="mb-4 h-1.5 w-full overflow-hidden rounded bg-slate-200" role="progressbar" aria-valuenow={progress.data.percent} aria-valuemin={0} aria-valuemax={100}><div className="h-full bg-brand-500 transition-all" style={{ width: `${progress.data.percent}%` }} /></div>}
      <ErrorBox error={act.error ?? del.error} />
      {d.error && <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800"><div className="font-medium">Processing problem</div><div className="mt-1 whitespace-pre-wrap break-words">{d.error}</div>
        {canWrite && failedJobs.map((j) => <button key={j.id} className="btn-secondary mt-2 mr-2" disabled={act.isPending} onClick={() => act.mutate({ job: j.id, action: "retry" })}>Retry {humanize(j.stage)} {j.page_start ? `(pages ${j.page_start}–${j.page_end})` : ""}</button>)}</div>}
      {canWrite && active && root && <button className="btn-secondary mb-4" onClick={() => confirm("Cancel processing?") && act.mutate({ job: root.id, action: "cancel" })}>Cancel processing</button>}

      <div className="mb-4 flex gap-1 border-b border-slate-200" role="tablist">
        {tabs.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? "border-brand-500 font-medium text-brand-700" : "border-transparent text-slate-600 hover:text-slate-900"}`}>{label}</button>)}
      </div>

      {tab === "invoices" && (invoices.isLoading ? <Loading /> : !invoices.data?.length ? <Empty>{active ? "Extraction in progress…" : "No invoices were extracted from this document."}</Empty> : (
        <div className="card overflow-x-auto"><table className="w-full">
          <thead className="border-b border-slate-200"><tr><th className="th">Invoice no.</th><th className="th">Supplier</th><th className="th">Date</th><th className="th text-right">Total</th><th className="th">Findings</th><th className="th">Review</th></tr></thead>
          <tbody className="divide-y divide-slate-100">{invoices.data.map((i) => {
            const open = i.findings.filter((f) => f.resolution_status === "open");
            return <tr key={i.id} className="hover:bg-slate-50">
              <td className="td"><Link className="font-medium text-brand-600 hover:underline" to={`/invoices/${i.id}`}>{i.invoice_number ?? <span className="text-slate-400">(missing)</span>}</Link>{i.row_ref && <div className="text-xs text-slate-500">{i.row_ref}</div>}</td>
              <td className="td">{i.supplier_name ?? "—"}</td><td className="td">{i.invoice_date ?? "—"}</td><td className="td text-right tabular-nums">{formatMoney(i.total, i.currency)}</td>
              <td className="td">{open.filter((f) => f.severity === "error").length > 0 && <Badge value="error" label={`${open.filter((f) => f.severity === "error").length} error`} />} {open.filter((f) => f.severity === "warning").length > 0 && <Badge value="warning" label={`${open.filter((f) => f.severity === "warning").length} warning`} />}{open.length === 0 && <span className="text-xs text-slate-400">none open</span>}</td>
              <td className="td"><Badge value={i.review_status} /></td></tr>;
          })}</tbody></table></div>
      ))}

      {tab === "pages" && (pages.isLoading ? <Loading /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="card max-h-[70vh] overflow-auto"><table className="w-full"><thead className="sticky top-0 border-b border-slate-200 bg-white"><tr><th className="th">Page</th><th className="th">Kind</th><th className="th">Status</th><th className="th">OCR confidence</th><th className="th">Chars</th></tr></thead>
            <tbody className="divide-y divide-slate-100">{pages.data?.map((p) => <tr key={p.page} className={`cursor-pointer hover:bg-slate-50 ${textPage === p.page ? "bg-brand-50" : ""}`} onClick={() => setTextPage(p.page)}>
              <td className="td tabular-nums">{p.label ?? p.page}</td><td className="td">{humanize(p.kind)}</td><td className="td"><Badge value={p.status === "ok" ? "succeeded" : p.status} label={humanize(p.status)} />{p.error && <div className="text-xs text-red-700">{p.error}</div>}</td>
              <td className="td"><Confidence score={p.ocr_confidence} /></td><td className="td tabular-nums">{p.text_chars}</td></tr>)}</tbody></table></div>
          <div className="card p-4"><h3 className="mb-2 text-sm font-semibold">Extracted text{textPage ? ` — page ${textPage}` : ""}</h3>
            {textPage === null ? <p className="text-sm text-slate-500">Select a page to see its text.</p> : pageText.isLoading ? <Loading /> : pageText.error ? <p className="text-sm text-slate-500">No text for this page.</p> :
              <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{pageText.data?.text}</pre>}</div>
        </div>
      ))}

      {tab === "jobs" && (jobs.isLoading ? <Loading /> : (
        <div className="card overflow-x-auto"><table className="w-full"><thead className="border-b border-slate-200"><tr><th className="th">Stage</th><th className="th">Pages</th><th className="th">Status</th><th className="th">Attempts</th><th className="th">Error</th><th className="th" /></tr></thead>
          <tbody className="divide-y divide-slate-100">{jobs.data?.map((j) => <tr key={j.id}><td className="td">{humanize(j.stage)}{j.parent_job_id === null && j.stage === "extract_text" ? " (plan)" : ""}</td><td className="td">{j.page_start ? `${j.page_start}–${j.page_end}` : "—"}</td><td className="td"><Badge value={j.status} /></td><td className="td tabular-nums">{j.attempts}/{j.max_attempts}</td><td className="td max-w-xs truncate text-xs text-red-700" title={j.error ?? ""}>{j.error}</td>
            <td className="td text-right">{canWrite && ["failed", "dead"].includes(j.status) && <button className="btn-secondary" onClick={() => act.mutate({ job: j.id, action: "retry" })}>Retry</button>}</td></tr>)}</tbody></table></div>
      ))}

      {tab === "history" && (history.isLoading ? <Loading /> : (
        <ol className="card divide-y divide-slate-100">{history.data?.map((h, n) => <li key={n} className="flex flex-wrap justify-between gap-2 px-4 py-2 text-sm"><span><span className="font-medium">{humanize(h.event)}</span> <span className="text-xs text-slate-500">by {h.actor.length > 12 ? "user" : h.actor}</span>{Object.keys(h.detail).length > 0 && <span className="ml-2 font-mono text-xs text-slate-500">{JSON.stringify(h.detail)}</span>}</span><span className="text-xs text-slate-500">{formatDateTime(h.at)}</span></li>)}</ol>
      ))}

      {tab === "file" && <div className="card h-[75vh] p-2"><DocumentViewer documentId={id} mime={d.mime_type} /></div>}
    </>
  );
}
