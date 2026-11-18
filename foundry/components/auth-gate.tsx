"use client";

import * as React from "react";
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { ArrowRight, KeyRound, Loader2, LockKeyhole, ShieldCheck } from "lucide-react";
import { Button, Card, CardContent, InlineNotice, Input, Label } from "@/components/ui";
import type { PublicConfig } from "@/lib/public-config";

type FoundrySession = {
  userId: string;
  email: string | null;
  accessToken: string;
  signOut: () => Promise<void>;
};

type FoundryContextValue = {
  config: PublicConfig;
  session: FoundrySession | null;
  apiFetch: <T = unknown>(path: string, init?: RequestInit) => Promise<T>;
};

const FoundryContext = React.createContext<FoundryContextValue | null>(null);

export function useFoundry(): FoundryContextValue {
  const value = React.useContext(FoundryContext);
  if (!value) throw new Error("useFoundry must be used inside AuthGate");
  return value;
}

export function useApiResource<T>(path: string | null, deps: React.DependencyList = []) {
  const { apiFetch } = useFoundry();
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(Boolean(path));
  const [nonce, setNonce] = React.useState(0);
  const refresh = React.useCallback(() => setNonce((v) => v + 1), []);
  React.useEffect(() => {
    let alive = true;
    if (!path) { setData(null); setLoading(false); return; }
    setLoading(true);
    apiFetch<T>(path)
      .then((value) => { if (alive) { setData(value); setError(null); } })
      .catch((err: unknown) => { if (alive) { setData(null); setError(err instanceof Error ? err.message : String(err)); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, apiFetch, ...deps]);
  return { data, error, loading, refresh };
}

export function AuthGate({ config, children }: { config: PublicConfig; children: React.ReactNode }) {
  const [supabase] = React.useState<SupabaseClient | null>(() => {
    if (!config.authConfigured) return null;
    return createClient(config.supabaseUrl, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      realtime: { params: { eventsPerSecond: 8 } }
    });
  });
  const [session, setSession] = React.useState<Session | null>(null);
  const [checking, setChecking] = React.useState(config.authConfigured);
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [mode, setMode] = React.useState<"signin" | "signup">("signin");
  const [authError, setAuthError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  React.useEffect(() => {
    if (!supabase) return;
    let mounted = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return;
      setSession(data.session);
      setChecking(false);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      setChecking(false);
    });
    return () => { mounted = false; listener.subscription.unsubscribe(); };
  }, [supabase]);

  const apiFetch = React.useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const headers = new Headers(init.headers);
    if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...init, headers, cache: "no-store" });
    const contentType = response.headers.get("content-type") ?? "";
    const payload: unknown = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
      throw new Error(String(record.error ?? record.message ?? `HTTP ${response.status}`));
    }
    return payload as T;
  }, [session?.access_token]);

  const value = React.useMemo<FoundryContextValue>(() => ({
    config,
    session: session ? {
      userId: session.user.id,
      email: session.user.email ?? null,
      accessToken: session.access_token,
      signOut: async () => { await supabase?.auth.signOut(); }
    } : null,
    apiFetch
  }), [config, session, apiFetch, supabase]);

  if (config.authConfigured && checking) {
    return <div className="flex min-h-screen items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-signal-cyan" /></div>;
  }

  if (config.authConfigured && !session) {
    return (
      <FoundryContext.Provider value={value}>
        <div className="flex min-h-screen items-center justify-center p-5">
          <Card className="w-full max-w-sm animate-fade-in">
            <div className="border-b border-line px-5 py-4">
              <div className="flex items-center gap-2 font-mono text-sm font-semibold tracking-[0.18em] text-signal-cyan"><ShieldCheck className="h-4 w-4" /> FOUNDRY</div>
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">Sign in to your personal agent workspace. Sessions are verified server-side on every API request.</p>
            </div>
            <CardContent className="space-y-3">
              <div><Label>Email</Label><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" /></div>
              <div><Label>Password</Label><Input type="password" autoComplete={mode === "signup" ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" /></div>
              {authError ? <InlineNotice tone="rose">{authError}</InlineNotice> : null}
              <Button
                className="w-full"
                disabled={submitting || !email || password.length < 6}
                onClick={async () => {
                  if (!supabase) return;
                  setSubmitting(true); setAuthError(null);
                  const result = mode === "signin"
                    ? await supabase.auth.signInWithPassword({ email, password })
                    : await supabase.auth.signUp({ email, password });
                  if (result.error) setAuthError(result.error.message);
                  else if (mode === "signup") setAuthError("Check your email to confirm the account, then sign in.");
                  setSubmitting(false);
                }}
              >
                {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
                {mode === "signin" ? "Sign in" : "Create account"}
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
              <button className="w-full text-center text-2xs text-ink-faint hover:text-ink-muted" onClick={() => setMode((m) => m === "signin" ? "signup" : "signin")}>
                {mode === "signin" ? "Need an account? Sign up" : "Already have an account? Sign in"}
              </button>
              <div className="flex items-center justify-center gap-1.5 pt-1 text-2xs text-ink-faint"><LockKeyhole className="h-3 w-3" /> Server-side credential boundary</div>
            </CardContent>
          </Card>
        </div>
      </FoundryContext.Provider>
    );
  }

  return <FoundryContext.Provider value={value}>{children}</FoundryContext.Provider>;
}
