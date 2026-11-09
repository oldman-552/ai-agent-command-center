/** Browser-side API client. Calls same-origin /api/* (proxied to FastAPI). */
export class ApiError extends Error {
  constructor(public status: number, public code: string) {
    super(code);
  }
}

function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie.split("; ").find((c) => c.startsWith("acc_csrf="))?.slice("acc_csrf=".length);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  const method = (init?.method ?? "GET").toUpperCase();
  const csrf = method === "GET" || method === "HEAD" ? undefined : csrfToken();
  try {
    res = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        ...(init?.headers ?? {}),
      },
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

export type User = {
  id: number;
  username: string;
  role: string;
  role_name_ar: string;
  role_name_en: string;
  language: "ar" | "en";
  organization_id: number;
  permissions: string[];
};
