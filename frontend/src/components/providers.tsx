"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useState } from "react";
import type { User } from "@/lib/api";

const UserContext = createContext<User | null>(null);

/** The signed-in user (server-verified in the (app) layout). */
export function useUser(): User {
  const u = useContext(UserContext);
  if (!u) throw new Error("useUser outside AppProviders");
  return u;
}

/** UI hint only — the backend enforces every permission. */
export function useCan() {
  const user = useUser();
  return (perm: string) => user.permissions.includes(perm);
}

export function AppProviders({ user, children }: { user: User; children: React.ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <UserContext.Provider value={user}>{children}</UserContext.Provider>
    </QueryClientProvider>
  );
}
