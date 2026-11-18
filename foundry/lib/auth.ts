import { createClient } from "@supabase/supabase-js";
import { ApiError } from "@/lib/errors";

export type AuthUser = {
  id: string;
  email: string | null;
  accessToken: string;
};

function bearer(request: Request): string {
  const value = request.headers.get("authorization") ?? "";
  const match = /^Bearer\s+(.+)$/i.exec(value);
  return match?.[1]?.trim() ?? "";
}

export async function requireUser(request: Request): Promise<AuthUser> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) throw new ApiError("Supabase Auth is not configured.", 503, "AUTH_NOT_CONFIGURED");
  const token = bearer(request);
  if (!token) throw new ApiError("Sign in required.", 401, "UNAUTHENTICATED");
  const client = createClient(url, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { headers: { Authorization: `Bearer ${token}` } }
  });
  const { data, error } = await client.auth.getUser(token);
  if (error || !data.user) throw new ApiError("Session is invalid or expired.", 401, "INVALID_SESSION");
  return { id: data.user.id, email: data.user.email ?? null, accessToken: token };
}

export function isAuthConfigured(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);
}
