import { useEffect, useMemo, useSyncExternalStore } from "react";
import { QueryClient, useQuery } from "@tanstack/react-query";
import { apiClient, hasActiveSessionAuthHeader } from "../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../services/browserSession";

export type CurrentUserRole = "admin" | "operator" | "";

// Existing callers need not be descendants of a QueryClientProvider.
const browserClients = new Map<string, QueryClient>();
const clientForBrowser = (session: string) => {
  let client = browserClients.get(session);
  if (!client) { client = new QueryClient(); browserClients.set(session, client); }
  return client;
};
export const refreshCurrentUserRole = async () => {
  const session = browserSessionKey();
  const client = browserClients.get(session);
  if (client) await client.invalidateQueries({ queryKey: ["current-user-role", session] });
};

export const useCurrentUserRole = () => {
  const session = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  const client = useMemo(() => typeof window === "undefined" ? new QueryClient() : clientForBrowser(session), [session]);
  useEffect(() => {
    if (session !== browserSessionKey()) return;
    // Cache notifications and cancellation belong to the committed auth boundary, not render.
    for (const [key, previous] of browserClients) {
      if (key === session) continue;
      void previous.cancelQueries();
      previous.clear();
      browserClients.delete(key);
    }
  }, [session]);
  const query = useQuery({
    queryKey: ["current-user-role", session],
    enabled: session !== "server" && hasActiveSessionAuthHeader(),
    retry: false, staleTime: 0, refetchOnMount: "always", refetchOnWindowFocus: false,
    queryFn: async ({ signal }) => {
      const response = await apiClient.get<{ role?: unknown }>("/auth/me", { signal });
      if (signal.aborted || session !== browserSessionKey() || !hasActiveSessionAuthHeader()) throw new Error("Authentication changed");
      const value = typeof response.data?.role === "string" ? response.data.role.trim().toLowerCase() : "";
      const role: CurrentUserRole = value === "admin" || value === "operator" ? value : "";
      return { role, session };
    },
  }, client);
  const loading = session === "server" || (hasActiveSessionAuthHeader() && query.isFetching);
  const role: CurrentUserRole = !loading && !query.isError && hasActiveSessionAuthHeader()
    && query.data?.session === session ? query.data.role : "";

  return {
    role,
    isAdmin: role === "admin",
    isOperator: role === "operator" || role === "admin",
    loading,
  };
};
