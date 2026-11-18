import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SearchResponse } from "../api/types";
import { Badge, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { formatMoney } from "../utils/format";

/** Snippets come back with <b> highlight tags only; everything else is escaped before rendering. */
export function safeSnippet(html: string): string {
  const esc = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/&lt;b&gt;/g, "<b>").replace(/&lt;\/b&gt;/g, "</b>");
}

export default function Search() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? ""; const mode = params.get("mode") ?? "hybrid";
  const [text, setText] = useState(q);
  const res = useQuery({ queryKey: ["search", q, mode], queryFn: () => api<SearchResponse>(`/search?q=${encodeURIComponent(q)}&mode=${mode}&limit=30`), enabled: q.trim().length > 0 });
  const submit = (e: FormEvent) => { e.preventDefault(); if (text.trim()) setParams({ q: text.trim(), mode }); };
  return (
    <>
      <PageHeader title="Search" subtitle="Full-text and similarity search across extracted text, plus invoice numbers and suppliers" />
      <form onSubmit={submit} className="mb-4 flex flex-wrap gap-2" role="search">
        <input className="input max-w-xl flex-1" aria-label="Search query" placeholder="Invoice number, supplier, phrase…" value={text} maxLength={500} onChange={(e) => setText(e.target.value)} />
        <select className="input max-w-[11rem]" aria-label="Search mode" value={mode} onChange={(e) => setParams({ q: text.trim() || q, mode: e.target.value })}>
          <option value="hybrid">Hybrid</option><option value="fts">Exact words</option><option value="vector">Similarity</option>
        </select>
        <button className="btn-primary">Search</button>
      </form>
      <ErrorBox error={res.error} />
      {res.isFetching && <Loading label="Searching…" />}
      {res.data && !res.isFetching && (
        <>
          {!res.data.semantic && res.data.mode !== "fts" && <p className="mb-4 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">Similarity uses the built-in word-overlap embedder (<span className="font-mono">{res.data.embedding_model}</span>), so it matches shared words, not meaning. Configure a sentence-transformers model for semantic search.</p>}
          {res.data.invoices.length > 0 && (
            <div className="mb-6"><h2 className="mb-2 text-sm font-semibold">Invoices ({res.data.invoices.length})</h2>
              <div className="card overflow-x-auto"><table className="w-full"><tbody className="divide-y divide-slate-100">{res.data.invoices.map((i) => (
                <tr key={i.invoice_id} className="hover:bg-slate-50"><td className="td"><Link className="font-medium text-brand-600 hover:underline" to={`/invoices/${i.invoice_id}`}>{i.invoice_number ?? "(no number)"}</Link></td><td className="td">{i.supplier_name ?? "—"}</td><td className="td text-right tabular-nums">{formatMoney(i.total, i.currency)}</td><td className="td"><Badge value={i.review_status} /></td><td className="td text-xs text-slate-500">{i.filename}</td></tr>))}</tbody></table></div></div>
          )}
          <h2 className="mb-2 text-sm font-semibold">Text matches ({res.data.results.length})</h2>
          {res.data.results.length === 0 ? <Empty>No text matches for “{res.data.query}”.</Empty> : (
            <ul className="space-y-3">{res.data.results.map((h) => (
              <li key={`${h.document_id}-${h.page}`} className="card p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2"><Link className="font-medium text-brand-600 hover:underline" to={`/documents/${h.document_id}`}>{h.filename}</Link><span className="text-xs text-slate-500">{h.page ? `page ${h.page} · ` : ""}{Object.entries(h.ranks).map(([k, v]) => `${k === "fts" ? "words" : "similarity"} #${v}`).join(" · ")}</span></div>
                <p className="mt-1 text-sm text-slate-700 [&_b]:rounded [&_b]:bg-yellow-100 [&_b]:px-0.5" dangerouslySetInnerHTML={{ __html: safeSnippet(h.snippet) }} />
              </li>))}</ul>
          )}
        </>
      )}
    </>
  );
}
