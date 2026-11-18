import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, post } from "../api/client";
import type { Role, User } from "../api/types";
import { Badge, ErrorBox, Loading, PageHeader } from "../components/ui";

const ROLES: Role[] = ["viewer", "uploader", "reviewer", "admin"];
const DESC: Record<Role, string> = { viewer: "Read own documents", uploader: "Upload and read own documents", reviewer: "Review, correct, reconcile all documents", admin: "Everything, including users" };

export default function Users() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users") });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "uploader" as Role });
  const create = useMutation({ mutationFn: () => post<User>("/users", { ...form, full_name: form.full_name || null }), onSuccess: () => { setForm({ email: "", full_name: "", password: "", role: "uploader" }); void qc.invalidateQueries({ queryKey: ["users"] }); } });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };
  return (
    <>
      <PageHeader title="Users" subtitle="Create accounts and assign roles" />
      <div className="grid gap-6 lg:grid-cols-3">
        <form onSubmit={submit} className="card space-y-3 p-4 lg:col-span-1" aria-label="Create user">
          <h2 className="text-sm font-semibold">New user</h2><ErrorBox error={create.error} />
          <label className="block text-xs font-medium">Email<input className="input mt-1" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
          <label className="block text-xs font-medium">Full name<input className="input mt-1" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
          <label className="block text-xs font-medium">Initial password (min 12 characters)<input className="input mt-1" type="password" autoComplete="new-password" required minLength={12} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
          <label className="block text-xs font-medium">Role<select className="input mt-1" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
          <p className="text-xs text-slate-500">{DESC[form.role]}</p>
          <button className="btn-primary w-full" disabled={create.isPending}>Create user</button>
        </form>
        <div className="lg:col-span-2">{users.isLoading ? <Loading /> : (
          <div className="card overflow-x-auto"><table className="w-full"><thead className="border-b border-slate-200"><tr><th className="th">Email</th><th className="th">Name</th><th className="th">Role</th><th className="th">Active</th></tr></thead>
            <tbody className="divide-y divide-slate-100">{users.data?.map((u) => <tr key={u.id}><td className="td">{u.email}</td><td className="td text-slate-600">{u.full_name ?? "—"}</td><td className="td"><Badge value={u.role} /></td><td className="td">{u.is_active ? "Yes" : "No"}</td></tr>)}</tbody></table></div>)}</div>
      </div>
    </>
  );
}
