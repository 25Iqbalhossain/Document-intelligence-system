import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Document } from "../api/types";
import { Badge, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { formatBytes, formatDateTime, humanize } from "../utils/format";

const STATUSES = ["uploaded", "extracting_text", "ai_extracting", "validating_financials", "needs_review", "processed", "failed", "cancelled"];
const PAGE = 25;

export default function Documents() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE + 1), offset: String(page * PAGE) });
  if (status) params.set("status", status);
  if (q.trim()) params.set("q", q.trim());
  const docs = useQuery({ queryKey: ["documents", status, q, page], queryFn: () => api<Document[]>(`/documents?${params}`), refetchInterval: 8_000 });
  const rows = docs.data?.slice(0, PAGE) ?? [];
  return (
    <>
      <PageHeader title="Documents" />
      <div className="mb-4 flex flex-wrap gap-2">
        <input className="input max-w-xs" placeholder="Filter by file name" aria-label="Filter by file name" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        <select className="input max-w-[14rem]" aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}>
          <option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
        </select>
      </div>
      <ErrorBox error={docs.error} />
      {docs.isLoading ? <Loading /> : rows.length === 0 ? <Empty>No documents match.</Empty> : (
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-slate-200"><tr><th className="th">File</th><th className="th">Type</th><th className="th">Pages</th><th className="th">Size</th><th className="th">Status</th><th className="th">Uploaded</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((d) => (
                <tr key={d.id} className="hover:bg-slate-50">
                  <td className="td"><Link className="font-medium text-brand-600 hover:underline" to={`/documents/${d.id}`}>{d.original_filename}</Link></td>
                  <td className="td text-slate-600">{humanize(d.doc_type)}</td><td className="td tabular-nums">{d.page_count ?? "—"}</td>
                  <td className="td text-slate-600">{formatBytes(d.size_bytes)}</td><td className="td"><Badge value={d.status} /></td>
                  <td className="td text-slate-500">{formatDateTime(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
        <button className="btn-secondary" disabled={(docs.data?.length ?? 0) <= PAGE} onClick={() => setPage(page + 1)}>Next</button>
      </div>
    </>
  );
}
