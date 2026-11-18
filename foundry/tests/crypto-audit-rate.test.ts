import { beforeAll, describe, expect, it } from "vitest";
import crypto from "node:crypto";

beforeAll(() => { process.env.FOUNDRY_SECRET_ENCRYPTION_KEY = Buffer.alloc(32, 7).toString("base64"); });

describe("security primitives", () => {
  it("round trips AES-256-GCM secrets and rejects tampering", async () => {
    const { encryptSecret, decryptSecret } = await import("@/lib/crypto");
    const record = encryptSecret("bridge-owner-token");
    expect(record.ciphertext).not.toContain("bridge-owner-token");
    expect(decryptSecret({ ...record, auth_tag: record.authTag, key_version: record.keyVersion })).toBe("bridge-owner-token");
    expect(() => decryptSecret({ ...record, auth_tag: crypto.randomBytes(16).toString("base64"), key_version: record.keyVersion })).toThrow();
  });
  it("redacts bearer tokens, keys and requestState", async () => {
    const { redactSecrets } = await import("@/lib/utils");
    const redacted = redactSecrets({ apiKey: "sk-abcdefgh123456", nested: { Authorization: "Bearer abc.def", requestState: "signed" } }) as Record<string, unknown>;
    expect(redacted.apiKey).toBe("[REDACTED]");
    expect((redacted.nested as Record<string, unknown>).requestState).toBe("[REDACTED]");
    expect(String((redacted.nested as Record<string, unknown>).Authorization)).toContain("REDACTED");
  });
  it("computes a deterministic tamper-evident audit hash", async () => {
    const { computeAuditHash } = await import("@/lib/audit");
    const prev = "0".repeat(64);
    const entry = { action: "tool.call", actor: "agent:1", target: "tool-1", outcome: "ok", detail: { tool: "get_state" } };
    const hash = computeAuditHash(prev, entry);
    expect(hash).toMatch(/^[a-f0-9]{64}$/);
    expect(computeAuditHash(prev, entry)).toBe(hash);
    expect(computeAuditHash(prev, { ...entry, outcome: "failed" })).not.toBe(hash);
  });
  it("enforces token-bucket rate decisions", async () => {
    const { computeRateLimitDecision } = await import("@/lib/rate-limit");
    const windowStart = 1_000_000;
    expect(computeRateLimitDecision(0, windowStart, 3, 60_000, windowStart + 100).allowed).toBe(true);
    expect(computeRateLimitDecision(3, windowStart, 3, 60_000, windowStart + 100).allowed).toBe(false);
    expect(computeRateLimitDecision(99, windowStart, 3, 60_000, windowStart + 60_001).allowed).toBe(true);
  });
});
