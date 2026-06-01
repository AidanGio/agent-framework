/**
 * Typed client for the agent-framework dashboard backend.
 *
 * Requests are sent with credentials so a `session` cookie (set by whatever
 * auth provider you wire into the backend) rides along on cross-origin calls.
 * In the template's no-auth dev mode there is no cookie — `/me` always
 * resolves to the configured local user.
 */

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(/\/$/, "");

if (!API_BASE && typeof window !== "undefined") {
  console.warn("VITE_DASHBOARD_API_BASE_URL is not set");
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SessionUser {
  id: string;
  name: string;
  email: string | null;
  avatar_url: string | null;
  is_admin: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  efforts: Array<string>;
  default_effort: string;
}

export interface Profile {
  id?: string;
  email?: string;
  default_model?: string;
  reasoning_effort?: string;
  updated_at?: string;
}

export interface ProfileUpdate {
  default_model: string;
  reasoning_effort: string;
}

export const api = {
  me: () => request<SessionUser>("/me"),
  options: () => request<{ models: Array<ModelOption> }>("/options"),
  profile: () => request<Profile>("/profile"),
  saveProfile: (body: ProfileUpdate) =>
    request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),
  adminListProfiles: () => request<Array<Profile>>("/admin/profiles"),
  adminSaveProfile: (userId: string, body: ProfileUpdate) =>
    request<Profile>(`/admin/profiles/${encodeURIComponent(userId)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};

export function loginUrl(redirectTo?: string): string {
  const target = redirectTo ?? (typeof window !== "undefined" ? window.location.origin : "");
  const qs = target ? `?redirect_to=${encodeURIComponent(target)}` : "";
  return `${API_BASE}/dashboard/api/auth/login${qs}`;
}
