import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { Role } from "./api/types";
import { useAuth } from "./auth/AuthContext";
import { Loading } from "./components/ui";
import { AppLayout } from "./layouts/AppLayout";
import Dashboard from "./pages/Dashboard";
import DocumentDetail from "./pages/DocumentDetail";
import Documents from "./pages/Documents";
import InvoiceReview from "./pages/InvoiceReview";
import Login from "./pages/Login";
import Reconciliation from "./pages/Reconciliation";
import ReviewQueue from "./pages/ReviewQueue";
import Search from "./pages/Search";
import Upload from "./pages/Upload";
import Users from "./pages/Users";

function Guard({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const { can } = useAuth();
  if (roles && !can(...roles)) return <div role="alert" className="card p-6 text-sm">You do not have permission to view this page.</div>;
  return <>{children}</>;
}

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading label="Starting…" />;
  if (!user) return <Routes><Route path="*" element={<Login />} /></Routes>;
  const staff: Role[] = ["admin", "reviewer"];
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="upload" element={<Guard roles={["admin", "reviewer", "uploader"]}><Upload /></Guard>} />
        <Route path="documents" element={<Documents />} />
        <Route path="documents/:id" element={<DocumentDetail />} />
        <Route path="invoices/:id" element={<InvoiceReview />} />
        <Route path="review" element={<Guard roles={staff}><ReviewQueue /></Guard>} />
        <Route path="search" element={<Search />} />
        <Route path="reconciliation" element={<Guard roles={staff}><Reconciliation /></Guard>} />
        <Route path="users" element={<Guard roles={["admin"]}><Users /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
