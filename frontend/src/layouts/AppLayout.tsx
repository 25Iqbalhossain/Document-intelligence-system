import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import type { Role } from "../api/types";
import { ChangePasswordModal } from "../components/ChangePasswordModal";

const NAV: { to: string; label: string; roles?: Role[] }[] = [
  { to: "/", label: "Dashboard" },
  { to: "/upload", label: "Upload", roles: ["admin", "reviewer", "uploader"] },
  { to: "/documents", label: "Documents" },
  { to: "/review", label: "Review queue", roles: ["admin", "reviewer"] },
  { to: "/search", label: "Search" },
  { to: "/reconciliation", label: "Reconciliation", roles: ["admin", "reviewer"] },
  { to: "/users", label: "Users", roles: ["admin"] },
];

export function AppLayout() {
  const { user, logout, can } = useAuth();
  const [pwModalOpen, setPwModalOpen] = useState(false);
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="px-5 py-5 text-sm font-semibold tracking-tight">Document Intelligence</div>
        <nav className="flex-1 space-y-0.5 px-2" aria-label="Main">
          {NAV.filter((n) => !n.roles || can(...n.roles)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => `block rounded-md px-3 py-2 text-sm ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-50"}`}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-200 p-4 text-xs">
          <div className="truncate font-medium text-slate-700" title={user?.email}>{user?.email}</div>
          <div className="mb-2 text-slate-500">{user?.role}</div>
          <div className="space-y-1.5">
            <button className="btn-secondary w-full" onClick={() => setPwModalOpen(true)}>Change password</button>
            <button className="btn-secondary w-full" onClick={() => void logout()}>Sign out</button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 md:hidden">
          <span className="text-sm font-semibold">Document Intelligence</span>
          <div className="flex gap-2">
            <button className="btn-secondary text-xs" onClick={() => setPwModalOpen(true)}>Password</button>
            <button className="btn-secondary text-xs" onClick={() => void logout()}>Sign out</button>
          </div>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-white px-2 py-1 md:hidden" aria-label="Mobile">
          {NAV.filter((n) => !n.roles || can(...n.roles)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => `whitespace-nowrap rounded px-3 py-1.5 text-sm ${isActive ? "bg-brand-50 text-brand-700" : "text-slate-600"}`}>{n.label}</NavLink>
          ))}
        </nav>
        <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-8"><Outlet /></main>
      </div>
      <ChangePasswordModal isOpen={pwModalOpen} onClose={() => setPwModalOpen(false)} />
    </div>
  );
}
