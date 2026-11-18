import { NextResponse } from "next/server";
import { redactSecrets } from "@/lib/utils";

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(message: string, status = 400, code = "BAD_REQUEST", details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function jsonOk<T>(data: T, status = 200): NextResponse {
  return NextResponse.json(redactSecrets(data), {
    status,
    headers: { "Cache-Control": "no-store, max-age=0", "X-Content-Type-Options": "nosniff" }
  });
}

export function jsonError(error: unknown): NextResponse {
  if (error instanceof ApiError) {
    return jsonOk({ error: error.message, code: error.code, details: redactSecrets(error.details) }, error.status);
  }
  const pg = error as { code?: string; message?: string; detail?: string };
  if (pg?.code === "42P01" || pg?.code === "42703" || pg?.code === "42883") {
    return jsonOk({ error: "FOUNDRY database migration is missing or out of date.", code: "MIGRATION_REQUIRED" }, 503);
  }
  if (pg?.code === "23505") return jsonOk({ error: "A record with this identifier already exists.", code: "CONFLICT" }, 409);
  if (pg?.code === "23503") return jsonOk({ error: "A referenced record does not exist.", code: "INVALID_REFERENCE" }, 400);
  if (pg?.code === "42501") return jsonOk({ error: "Database policy denied this operation.", code: "RLS_DENIED" }, 403);
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes("DATABASE_NOT_CONFIGURED")) {
    return jsonOk({ error: "DATABASE_URL is not configured. Apply the migration and set the server-side connection string.", code: "DATABASE_NOT_CONFIGURED" }, 503);
  }
  if (message.includes("AUTH_NOT_CONFIGURED")) {
    return jsonOk({ error: "Supabase Auth is not configured.", code: "AUTH_NOT_CONFIGURED" }, 503);
  }
  console.error("[foundry:api]", redactSecrets(message));
  return jsonOk({ error: redactSecrets(message) || "Unexpected server error", code: "INTERNAL_ERROR" }, 500);
}

export async function readJson<T = Record<string, unknown>>(request: Request): Promise<T> {
  try {
    const value = await request.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiError("Expected a JSON object.");
    return value as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Invalid JSON body.", 400, "INVALID_JSON");
  }
}

export function requireString(value: unknown, field: string, max = 500): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) throw new ApiError(`${field} is required and must be at most ${max} characters.`);
  return value.trim();
}

export function optionalString(value: unknown, field: string, max = 10_000, fallback = ""): string {
  if (value == null || value === "") return fallback;
  if (typeof value !== "string" || value.length > max) throw new ApiError(`${field} must be a string of at most ${max} characters.`);
  return value;
}

export function requireObject(value: unknown, field = "value"): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiError(`${field} must be an object.`);
  return value as Record<string, unknown>;
}

export function jsonb(value: unknown): string {
  return JSON.stringify(value ?? {});
}
