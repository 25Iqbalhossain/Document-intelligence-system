import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, hasRefreshToken, login as apiLogin, logout as apiLogout, restoreSession, setAuthLostHandler } from "../api/client";
import type { Role, User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (...roles: Role[]) => boolean;
}
const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const clear = useCallback(() => { setUser(null); qc.clear(); }, [qc]);

  useEffect(() => {
    setAuthLostHandler(clear);
    (async () => {
      try {
        if (hasRefreshToken() && (await restoreSession())) setUser(await api<User>("/auth/me"));
      } catch { /* fall through to login */ }
      setLoading(false);
    })();
  }, [clear]);

  const value = useMemo<AuthState>(() => ({
    user, loading,
    login: async (email, password) => { await apiLogin(email, password); setUser(await api<User>("/auth/me")); },
    logout: async () => { await apiLogout(); clear(); navigate("/", { replace: true }); },
    can: (...roles) => !!user && roles.includes(user.role),
  }), [user, loading, clear, navigate]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
