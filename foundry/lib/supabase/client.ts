"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let cached: SupabaseClient | null | undefined;

export function getBrowserSupabase(): SupabaseClient | null {
  if (cached !== undefined) return cached;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  cached = url && key ? createClient(url, key, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    realtime: { params: { eventsPerSecond: 8 } }
  }) : null;
  return cached;
}

export function getRealtimeSupabase(accessToken: string): SupabaseClient | null {
  const client = getBrowserSupabase();
  if (!client) return null;
  void client.realtime.setAuth(accessToken);
  return client;
}
