import { describe, expect, it } from "vitest";
import { boundedJson } from "@/lib/utils";

describe("boundedJson", () => {
  it("returns ordinary JSON unchanged", () => {
    expect(boundedJson({ ok: true, count: 2 })).toBe('{"ok":true,"count":2}');
    expect(boundedJson(null)).toBe("null");
    expect(boundedJson(undefined)).toBe("null");
  });

  it("wraps oversized output in valid JSON instead of cutting an escape", () => {
    const value = { text: "x".repeat(5_000), nested: { token: "Bearer example-token" } };
    const json = boundedJson(value, 600);
    expect(json.length).toBeLessThanOrEqual(600);
    const parsed = JSON.parse(json) as { truncated?: boolean; originalChars?: number; preview?: string };
    expect(parsed.truncated).toBe(true);
    expect(parsed.originalChars).toBeGreaterThan(600);
    expect(typeof parsed.preview).toBe("string");
  });

  it("returns valid JSON for circular values", () => {
    const value: Record<string, unknown> = { ok: true };
    value.self = value;
    const parsed = JSON.parse(boundedJson(value)) as { error?: string };
    expect(parsed.error).toContain("could not be serialized");
  });
});
