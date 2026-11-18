import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Document, Stats } from "../api/types";
import { Badge, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { formatDateTime } from "../utils/format";

export default function Dashboard() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api<Stats>("/stats"), refetchInterval: 10_000 });
  const recent = useQuery({ queryKey: ["documents", "recent"], queryFn: () => api<Document[]>("/documents?limit=8"), refetchInterval: 10_000 });
  if (stats.isLoading) return <Loading />;
  const s = stats.data;
  return (
    <>
      <PageHeader title="Dashboard" subtitle="Processing and review status at a glance" />
      <ErrorBox error={stats.error ?? recent.error} />
      {s && (
        <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Documents" value={s.documents} />
          <Stat label="Needs review" value={s.by_status.needs_review ?? 0} tone={(s.by_status.needs_review ?? 0) > 0 ? "warn" : undefined} />
          <Stat label="Invoices awaiting review" value={s.invoices_pending_review} tone={s.invoices_pending_review > 0 ? "warn" : undefined} />
          <Stat label="Failed jobs" value={s.failed_jobs} tone={s.failed_jobs > 0 ? "bad" : undefined} />
        </div>
      )}
      <h2 className="mb-2 text-sm font-semibold">Recent documents</h2>
      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead className="border-b border-slate-200"><tr><th className="th">File</th><th className="th">Status</th><th className="th">Uploaded</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {recent.data?.map((d) => (
              <tr key={d.id}><td className="td"><Link className="text-brand-600 hover:underline" to={`/documents/${d.id}`}>{d.original_filename}</Link></td>
                <td className="td"><Badge value={d.status} /></td><td className="td text-slate-500">{formatDateTime(d.created_at)}</td></tr>
            ))}
            {recent.data?.length === 0 && <tr><td className="td py-8 text-center text-slate-500" colSpan={3}>No documents yet. Upload your first one.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
