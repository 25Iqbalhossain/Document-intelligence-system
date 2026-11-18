// Access token lives in memory only. The refresh token is kept in sessionStorage (cleared when the tab closes):
// a deliberate trade-off — it survives a page reload but is not shared across tabs or persisted on disk.
const REFRESH_KEY = "docint.refresh";
let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onAuthLost: () => void = () => {};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export const setAuthLostHandler = (fn: () => void) => { onAuthLost = fn; };
export const hasRefreshToken = () => { try { return !!sessionStorage.getItem(REFRESH_KEY); } catch { return false; } };
const store = (refresh: string | null) => {
  try { refresh ? sessionStorage.setItem(REFRESH_KEY, refresh) : sessionStorage.removeItem(REFRESH_KEY); } catch { /* storage unavailable */ }
};

export function setTokens(access: string | null, refresh: string | null) { accessToken = access; store(refresh); }

async function refreshTokens(): Promise<boolean> {
  let rt: string | null = null;
  try { rt = sessionStorage.getItem(REFRESH_KEY); } catch { /* ignore */ }
  if (!rt) return false;
  refreshing ??= fetch("/api/v1/auth/refresh", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: rt }),
  }).then(async (r) => {
    if (!r.ok) { setTokens(null, null); return false; }
    const d = await r.json();
    setTokens(d.access_token, d.refresh_token);
    return true;
  }).catch(() => false).finally(() => { refreshing = null; });
  return refreshing;
}

export async function errorMessage(r: Response): Promise<string> {
  try {
    const d = await r.json();
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail)) return d.detail.map((e: { loc?: unknown[]; msg: string }) => `${e.loc?.slice(1).join(".") ?? ""} ${e.msg}`.trim()).join("; ");
  } catch { /* not json */ }
  return `${r.status} ${r.statusText}`;
}

export async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const r = await fetch(`/api/v1${path}`, { ...init, headers });
  if (r.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refreshTokens()) return raw(path, init, false);
    onAuthLost();
  }
  return r;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const r = await raw(path, { ...init, headers });
  if (!r.ok) throw new ApiError(r.status, await errorMessage(r));
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export async function login(email: string, password: string) {
  const r = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
  if (!r.ok) throw new ApiError(r.status, r.status === 429 ? "Too many attempts. Try again in a minute." : await errorMessage(r));
  const d = await r.json();
  setTokens(d.access_token, d.refresh_token);
}

export async function restoreSession(): Promise<boolean> { return refreshTokens(); }

export async function logout() {
  let rt: string | null = null;
  try { rt = sessionStorage.getItem(REFRESH_KEY); } catch { /* ignore */ }
  if (rt) await fetch("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: rt }) }).catch(() => {});
  setTokens(null, null);
}

/** Authenticated download as a blob URL (files are never reachable by a public link). */
export async function fileBlobUrl(documentId: string): Promise<{ url: string; type: string }> {
  const r = await raw(`/documents/${documentId}/file`);
  if (!r.ok) throw new ApiError(r.status, await errorMessage(r));
  const b = await r.blob();
  return { url: URL.createObjectURL(b), type: b.type };
}

export async function downloadBlob(path: string, filename: string) {
  const r = await raw(path);
  if (!r.ok) throw new ApiError(r.status, await errorMessage(r));
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
