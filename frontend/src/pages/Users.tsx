import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, patch, post } from "../api/client";
import type { Role, User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, ErrorBox, Loading, PageHeader } from "../components/ui";

const ROLES: Role[] = ["viewer", "uploader", "reviewer", "admin"];
const DESC: Record<Role, string> = {
  viewer: "Read own documents",
  uploader: "Upload and read own documents",
  reviewer: "Review, correct, reconcile all documents",
  admin: "Everything, including users",
};

export default function Users() {
  const qc = useQueryClient();
  const { user: currentUser } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users") });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "uploader" as Role });
  const [resetUser, setResetUser] = useState<User | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [resetError, setResetError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => post<User>("/users", { ...form, full_name: form.full_name || null }),
    onSuccess: () => {
      setForm({ email: "", full_name: "", password: "", role: "uploader" });
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });

  const updateUser = useMutation({
    mutationFn: ({ id, ...body }: { id: string; is_active?: boolean; role?: Role; password?: string }) =>
      patch<User>(`/users/${id}`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  const handleResetPassword = async (e: FormEvent) => {
    e.preventDefault();
    if (!resetUser) return;
    if (resetPassword.length < 12) {
      setResetError("Password must be at least 12 characters");
      return;
    }
    setResetError(null);
    try {
      await updateUser.mutateAsync({ id: resetUser.id, password: resetPassword });
      setResetUser(null);
      setResetPassword("");
    } catch (err) {
      setResetError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <PageHeader title="Users" subtitle="Create accounts, assign roles, and manage access" />
      <div className="grid gap-6 lg:grid-cols-3">
        <form onSubmit={submit} className="card space-y-3 p-4 lg:col-span-1" aria-label="Create user">
          <h2 className="text-sm font-semibold">New user</h2>
          <ErrorBox error={create.error} />
          <label className="block text-xs font-medium">
            Email
            <input
              className="input mt-1"
              type="email"
              required
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </label>
          <label className="block text-xs font-medium">
            Full name
            <input
              className="input mt-1"
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            />
          </label>
          <label className="block text-xs font-medium">
            Initial password (min 12 characters)
            <input
              className="input mt-1"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </label>
          <label className="block text-xs font-medium">
            Role
            <select
              className="input mt-1"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <p className="text-xs text-slate-500">{DESC[form.role]}</p>
          <button className="btn-primary w-full" disabled={create.isPending}>
            Create user
          </button>
        </form>

        <div className="lg:col-span-2 space-y-3">
          <ErrorBox error={updateUser.error} />
          {users.isLoading ? (
            <Loading />
          ) : (
            <div className="card overflow-x-auto">
              <table className="w-full">
                <thead className="border-b border-slate-200">
                  <tr>
                    <th className="th">Email</th>
                    <th className="th">Name</th>
                    <th className="th">Role</th>
                    <th className="th">Status</th>
                    <th className="th text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {users.data?.map((u) => {
                    const isSelf = u.id === currentUser?.id;
                    return (
                      <tr key={u.id}>
                        <td className="td font-medium text-slate-800">{u.email}</td>
                        <td className="td text-slate-600">{u.full_name ?? "—"}</td>
                        <td className="td">
                          {isSelf ? (
                            <Badge value={u.role} />
                          ) : (
                            <select
                              className="input py-0.5 px-2 text-xs w-auto inline-block"
                              value={u.role}
                              disabled={updateUser.isPending}
                              onChange={(e) =>
                                updateUser.mutate({ id: u.id, role: e.target.value as Role })
                              }
                            >
                              {ROLES.map((r) => (
                                <option key={r} value={r}>
                                  {r}
                                </option>
                              ))}
                            </select>
                          )}
                        </td>
                        <td className="td">
                          <Badge
                            value={u.is_active ? "active" : "disabled"}
                            label={u.is_active ? "Active" : "Disabled"}
                          />
                        </td>
                        <td className="td text-right">
                          {isSelf ? (
                            <span className="text-xs text-slate-400">Current account</span>
                          ) : (
                            <div className="inline-flex items-center gap-1.5">
                              <button
                                type="button"
                                className="btn-secondary py-1 px-2 text-xs"
                                disabled={updateUser.isPending}
                                onClick={() => {
                                  setResetUser(u);
                                  setResetPassword("");
                                  setResetError(null);
                                }}
                              >
                                Reset password
                              </button>
                              <button
                                type="button"
                                className={`py-1 px-2 text-xs font-medium rounded transition-colors ${
                                  u.is_active
                                    ? "bg-red-50 text-red-700 hover:bg-red-100"
                                    : "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                                }`}
                                disabled={updateUser.isPending}
                                onClick={() =>
                                  updateUser.mutate({ id: u.id, is_active: !u.is_active })
                                }
                              >
                                {u.is_active ? "Disable" : "Enable"}
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {resetUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="text-base font-semibold text-slate-800">
                Reset password for {resetUser.email}
              </h3>
              <button
                type="button"
                className="text-slate-400 hover:text-slate-600 text-lg leading-none"
                onClick={() => setResetUser(null)}
              >
                &times;
              </button>
            </div>
            <form onSubmit={handleResetPassword} className="mt-4 space-y-4">
              <ErrorBox error={resetError} />
              <div>
                <label className="block text-xs font-medium text-slate-700">
                  New password (min 12 characters)
                </label>
                <input
                  type="password"
                  required
                  minLength={12}
                  className="input mt-1 w-full"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  placeholder="Enter new password"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setResetUser(null)}
                  disabled={updateUser.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={updateUser.isPending}
                >
                  {updateUser.isPending ? "Saving..." : "Save password"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
