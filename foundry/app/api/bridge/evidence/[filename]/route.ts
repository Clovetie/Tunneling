import { jsonOk } from "@/lib/errors";
import { getPublicConfig } from "@/lib/public-config";
import { TunnelingClient } from "@/lib/tunneling/client";
import { assertSafePathSegment, redactSecrets } from "@/lib/utils";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ filename: string }> };

export async function GET(_request: Request, context: RouteContext) {
  const config = getPublicConfig();
  if (!config.bridgePreviewEnabled) return jsonOk({ error: "Read-only bridge preview is disabled.", code: "BRIDGE_PREVIEW_DISABLED" }, 503);
  const { filename } = await context.params;
  try {
    const safe = assertSafePathSegment(filename);
    if (!/\.(png|jpe?g)$/i.test(safe)) throw new Error("Only PNG/JPEG evidence files are allowed");
    const client = new TunnelingClient({
      baseUrl: process.env.FOUNDRY_TUNNELING_URL!.trim(),
      token: process.env.FOUNDRY_TUNNELING_TOKEN!.trim(),
      timeoutMs: 10_000
    });
    const { bytes, contentType } = await client.evidence(safe);
    if (!contentType.toLowerCase().startsWith("image/")) throw new Error("Bridge returned a non-image evidence response");
    return new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Length": String(bytes.byteLength),
        "Cache-Control": "no-store, max-age=0",
        "X-Content-Type-Options": "nosniff"
      }
    });
  } catch (error) {
    return jsonOk({ error: redactSecrets(error instanceof Error ? error.message : String(error)), code: "EVIDENCE_PREVIEW_FAILED" }, 502);
  }
}
