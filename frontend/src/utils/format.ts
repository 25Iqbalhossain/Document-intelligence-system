/** Money is kept as the exact decimal string from the API; it is formatted by string operations, never via float. */
export function formatMoney(value: string | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!m) return value;
  const [, sign, int, frac = ""] = m;
  const trimmed = frac.replace(/0+$/, "").padEnd(2, "0");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${grouped}.${trimmed}${currency ? ` ${currency}` : ""}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function confidenceLabel(score: number | null | undefined): "high" | "medium" | "low" | "none" {
  if (score === null || score === undefined) return "none";
  return score >= 0.85 ? "high" : score >= 0.7 ? "medium" : "low";
}

/** Fields whose edited value differs from the original; only these are sent as corrections. */
export function diffFields(original: Record<string, string | null>, edited: Record<string, string>): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const [k, v] of Object.entries(edited)) {
    const before = original[k] ?? "";
    const norm = (x: string) => x.trim();
    if (norm(v) !== norm(before)) out[k] = norm(v) === "" ? null : norm(v);
  }
  return out;
}

export const ACCEPTED_EXT = [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx", ".xls", ".csv"];
export const extOk = (name: string) => ACCEPTED_EXT.some((e) => name.toLowerCase().endsWith(e));
