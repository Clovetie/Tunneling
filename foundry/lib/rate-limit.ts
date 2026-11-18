import { query } from "@/lib/db";
import { ApiError } from "@/lib/errors";

export type RateLimitDecision = { allowed: boolean; count: number; remaining: number; resetAt: number };

export function computeRateLimitDecision(count: number, windowStart: number, limit: number, windowMs: number, now = Date.now()): RateLimitDecision {
  const expired = now - windowStart >= windowMs;
  const effective = expired ? 0 : count;
  return {
    allowed: effective < limit,
    count: effective + 1,
    remaining: Math.max(0, limit - effective - 1),
    resetAt: (expired ? Math.floor(now / windowMs) * windowMs : windowStart) + windowMs
  };
}

export async function enforceRateLimit(ownerId: string, action: string, limit = 120, windowMs = 60_000): Promise<RateLimitDecision> {
  const now = Date.now();
  const windowStart = new Date(Math.floor(now / windowMs) * windowMs);
  const rows = await query<{ count: number }>(
    `insert into public.rate_limits (owner_id, action_key, window_start, count)
     values ($1,$2,$3,1)
     on conflict (owner_id, action_key, window_start)
     do update set count = public.rate_limits.count + 1, updated_at = now()
     returning count`,
    [ownerId, action, windowStart]
  );
  const count = rows[0]?.count ?? 1;
  const decision = computeRateLimitDecision(count - 1, windowStart.getTime(), limit, windowMs, now);
  if (!decision.allowed) throw new ApiError("Rate limit exceeded. Try again shortly.", 429, "RATE_LIMITED", decision);
  await query("delete from public.rate_limits where window_start < now() - interval '2 hours'").catch(() => undefined);
  return decision;
}
