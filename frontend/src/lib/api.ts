/** Browser-side API client. Calls same-origin /api/* (proxied to FastAPI). */
export class ApiError extends Error {
  constructor(public status: number, public code: string) {
    super(code);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/login")) {
    window.location.assign("/login");
  }
  if (!res.ok) {
    let code = "error";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") code = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export type User = { id: number; username: string; role: string; language: "ar" | "en" };
