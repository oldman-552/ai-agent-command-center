import "server-only";
import { cookies } from "next/headers";
import type { User } from "./api";

const API = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

/** Resolve the signed-in user on the server, or null. */
export async function getSessionUser(): Promise<User | null> {
  const session = (await cookies()).get("acc_session")?.value;
  if (!session) return null;
  try {
    const res = await fetch(`${API}/api/auth/me`, {
      headers: { cookie: `acc_session=${session}` },
      cache: "no-store",
    });
    return res.ok ? ((await res.json()) as User) : null;
  } catch {
    return null;
  }
}
