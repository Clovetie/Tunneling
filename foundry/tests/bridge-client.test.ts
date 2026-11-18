import { describe, expect, it } from "vitest";
import { TunnelingClient, summarizeTunnelingHealth } from "@/lib/tunneling/client";

describe("Tunneling REST adapter", () => {
  it("forwards the scoped bridge token on both supported headers for a Bus write", async () => {
    let seen: { url: string; init?: RequestInit } | null = null;
    const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
      seen = { url: String(url), init };
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    const client = new TunnelingClient({ baseUrl: "http://bridge.example/", token: "scoped-token", fetchImpl });
    const result = await client.post("/api/bus", { action: "post", body: "hello" });
    expect(result).toEqual({ ok: true });
    expect(seen).not.toBeNull();
    const captured = seen as unknown as { url: string; init?: RequestInit };
    expect(captured.url).toBe("http://bridge.example/api/bus");
    expect(captured.init?.method).toBe("POST");
    const headers = new Headers(captured.init?.headers);
    expect(headers.get("authorization")).toBe("Bearer scoped-token");
    expect(headers.get("x-arena-token")).toBe("scoped-token");
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("summarizes array-valued peer counts and nested tunnel state", () => {
    const summary = summarizeTunnelingHealth({
      ok: true,
      version: "6.0.0",
      mcp_tools: 101,
      studio_connected: true,
      peers: [{ peerId: "a" }, { peerId: "b" }],
      authMode: "scoped",
      tunnel: { state: "running", publicUrl: "https://bridge.example" },
      confirmGate: { mode: "write" }
    });
    expect(summary.peerCount).toBe(2);
    expect(summary.mcpTools).toBe(101);
    expect(summary.tunnelState).toBe("running");
    expect(summary.authMode).toBe("scoped");
    expect(summary.studioConnected).toBe(true);
  });

  it("rejects unsafe evidence filenames before making a bridge request", async () => {
    const client = new TunnelingClient({ baseUrl: "http://bridge.example", fetchImpl: (async () => { throw new Error("network must not be called"); }) as typeof fetch });
    await expect(client.evidence("../etc/passwd")).rejects.toThrow(/Unsafe path segment/);
  });
});
