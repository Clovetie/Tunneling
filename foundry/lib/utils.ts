import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function shortId(value: string | null | undefined, size = 8): string {
  if (!value) return "—";
  return value.length <= size ? value : `${value.slice(0, size)}…`;
}

export function compactNumber(value: number | string | null | undefined): string {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n)) return "0";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

export function formatTime(value: string | number | Date | null | undefined): string {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en", {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(date);
}

export function formatRelative(value: string | number | Date | null | undefined): string {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  const diff = Date.now() - date.getTime();
  const seconds = Math.round(diff / 1000);
  if (Math.abs(seconds) < 5) return "now";
  if (seconds < 0) return `in ${Math.abs(seconds)}s`;
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function safeJson(value: unknown): string {
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

/**
 * Serialize a value into a valid JSON string that is safe to store in a JSONB column.
 * Truncating JSON.stringify directly can cut through a string escape and produce invalid JSON;
 * this wraps oversized content in a valid object with a string preview instead.
 */
export function boundedJson(value: unknown, maxChars = 100_000): string {
  const limit = Math.max(256, Math.min(Math.floor(maxChars), 1_000_000));
  let json: string;
  try {
    json = JSON.stringify(value === undefined ? null : value) ?? "null";
  } catch (error) {
    return JSON.stringify({ error: "Value could not be serialized", detail: String(error instanceof Error ? error.message : error).slice(0, 200) });
  }
  if (json.length <= limit) return json;
  return JSON.stringify({ truncated: true, originalChars: json.length, preview: json.slice(0, Math.max(0, limit - 256)) });
}

const SENSITIVE_KEY = /(token|secret|password|api[-_]?key|authorization|credential|bearer|private[-_]?key|request[-_]?state)/i;

export function redactSecrets<T>(value: T, depth = 0): T {
  if (depth > 8 || value == null) return value;
  if (typeof value === "string") {
    return value
      .replace(/Bearer\s+[A-Za-z0-9._~+/-]+=*/gi, "Bearer [REDACTED]")
      .replace(/(sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{8,})/g, "[REDACTED]") as T;
  }
  if (Array.isArray(value)) return value.map((v) => redactSecrets(v, depth + 1)) as T;
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
      out[key] = SENSITIVE_KEY.test(key) ? "[REDACTED]" : redactSecrets(nested, depth + 1);
    }
    return out as T;
  }
  return value;
}

export function assertSafePathSegment(value: string): string {
  if (!/^[A-Za-z0-9._-]+$/.test(value) || value === "." || value === "..") {
    throw new Error("Unsafe path segment");
  }
  return value;
}

export function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function parseJsonObject(value: string, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value);
    return asObject(parsed);
  } catch {
    return fallback;
  }
}
