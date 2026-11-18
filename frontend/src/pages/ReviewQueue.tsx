import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { QueueItem } from "../api/types";
import { Badge, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { formatMoney } from "../utils/format";

export default function ReviewQueue() {
  const q = useQuery({ queryKey: ["review-queue"], queryFn: () => api<QueueItem[]>("/review/queue?limit=100"), refetchInterval: 15_000 });
  return (
    <>
      <PageHeader title="Review queue" subtitle="Invoices that need a human decision, oldest first" />
      <ErrorBox error={q.error} />
      {q.isLoading ? <Loading /> : !q.data?.length ? <Empty>Nothing to review. 🎉 Everything is either approved or passed all checks.</Empty> : (
        <div className="card overflow-x-auto"><table className="w-full">
          <thead className="border-b border-slate-200"><tr><th className="th">Invoice no.</th><th className="th">Supplier</th><th className="th text-right">Total</th><th className="th">Open issues</th><th className="th" /></tr></thead>
          <tbody className="divide-y divide-slate-100">{q.data.map((i) => (
            <tr key={i.invoice_id} className="hover:bg-slate-50">
              <td className="td font-medium">{i.invoice_number ?? <span className="text-slate-400">(missing)</span>}</td><td className="td">{i.supplier_name ?? "—"}</td>
              <td className="td text-right tabular-nums">{formatMoney(i.total)}</td>
              <td className="td space-x-1">{i.open_errors > 0 && <Badge value="error" label={`${i.open_errors} error${i.open_errors > 1 ? "s" : ""}`} />}{i.open_warnings > 0 && <Badge value="warning" label={`${i.open_warnings} warning${i.open_warnings > 1 ? "s" : ""}`} />}{i.open_errors + i.open_warnings === 0 && <span className="text-xs text-slate-500">low confidence</span>}</td>
              <td className="td text-right"><Link className="btn-primary" to={`/invoices/${i.invoice_id}`}>Review</Link></td>
            </tr>))}</tbody></table></div>
      )}
    </>
  );
}
