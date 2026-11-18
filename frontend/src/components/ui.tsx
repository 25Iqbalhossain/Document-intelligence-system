import type { ReactNode } from "react";
import { humanize } from "../utils/format";

const TONES: Record<string, string> = {
  processed: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  succeeded: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  confirmed: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  reconciled: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  needs_review: "bg-amber-50 text-amber-800 ring-amber-600/20",
  pending: "bg-amber-50 text-amber-800 ring-amber-600/20",
  proposed: "bg-amber-50 text-amber-800 ring-amber-600/20",
  needs_confirmation: "bg-amber-50 text-amber-800 ring-amber-600/20",
  warning: "bg-amber-50 text-amber-800 ring-amber-600/20",
  failed: "bg-red-50 text-red-700 ring-red-600/20",
  dead: "bg-red-50 text-red-700 ring-red-600/20",
  rejected: "bg-red-50 text-red-700 ring-red-600/20",
  error: "bg-red-50 text-red-700 ring-red-600/20",
  discrepancies: "bg-red-50 text-red-700 ring-red-600/20",
  open: "bg-red-50 text-red-700 ring-red-600/20",
  active: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  disabled: "bg-slate-100 text-slate-600 ring-slate-500/20",
  cancelled: "bg-slate-100 text-slate-600 ring-slate-500/20",
  info: "bg-sky-50 text-sky-700 ring-sky-600/20",
};
export function Badge({ value, label }: { value: string; label?: string }) {
  const tone = TONES[value] ?? "bg-slate-100 text-slate-700 ring-slate-500/20";
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}>{label ?? humanize(value)}</span>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="py-8 text-center text-sm text-slate-500" role="status">{label}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-300 py-12 text-center text-sm text-slate-500">{children}</div>;
}

export function Confidence({ score, status }: { score: number | null | undefined; status?: string }) {
  if (score === null || score === undefined) return <span className="text-xs text-slate-400">{status === "missing" ? "not found" : "n/a"}</span>;
  const pct = Math.round(score * 100);
  const color = score >= 0.85 ? "bg-emerald-500" : score >= 0.7 ? "bg-amber-500" : "bg-red-500";
  return (
    <span className="inline-flex items-center gap-1.5" title={`${pct}% confidence${status ? ` (${status})` : ""}`}>
      <span className="h-1.5 w-12 overflow-hidden rounded bg-slate-200"><span className={`block h-full ${color}`} style={{ width: `${pct}%` }} /></span>
      <span className="text-xs tabular-nums text-slate-600">{pct}%</span>
    </span>
  );
}

export function Stat({ label, value, tone }: { label: string; value: number | string; tone?: "warn" | "bad" }) {
  const color = tone === "bad" ? "text-red-600" : tone === "warn" ? "text-amber-600" : "text-slate-900";
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
