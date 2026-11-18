import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { ErrorBox } from "../components/ui";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await login(email, password); } catch (err) { setError(err); setBusy(false); }
  }
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6" aria-labelledby="login-title">
        <div>
          <h1 id="login-title" className="text-lg font-semibold tracking-tight">Document Intelligence</h1>
          <p className="text-sm text-slate-500">Sign in to continue</p>
        </div>
        <ErrorBox error={error} />
        <label className="block text-sm font-medium">Email
          <input className="input mt-1" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block text-sm font-medium">Password
          <input className="input mt-1" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
