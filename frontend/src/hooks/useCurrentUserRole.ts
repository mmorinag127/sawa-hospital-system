import { useEffect, useState } from "react";

import { apiClient } from "../services/apiClient";

export type CurrentUserRole = "admin" | "operator" | "";

type AuthMeResponse = {
  role?: string;
};

export const useCurrentUserRole = () => {
  const [role, setRole] = useState<CurrentUserRole>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<AuthMeResponse>("/auth/me")
      .then((res) => {
        if (cancelled) return;
        const nextRole = String(res.data?.role || "").trim().toLowerCase();
        setRole(nextRole === "admin" ? "admin" : "operator");
      })
      .catch(() => {
        if (cancelled) return;
        setRole("");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return {
    role,
    isAdmin: role === "admin",
    isOperator: role === "operator" || role === "admin",
    loading,
  };
};
