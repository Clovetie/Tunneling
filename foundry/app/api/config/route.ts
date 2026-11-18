import { getPublicConfig } from "@/lib/public-config";
import { jsonOk } from "@/lib/errors";
import { configuredProviders } from "@/lib/ai/providers";
import { encryptionFingerprint } from "@/lib/crypto";

export const dynamic = "force-dynamic";

export async function GET() {
  return jsonOk({ ...getPublicConfig(), providers: configuredProviders(), encryptionFingerprint: encryptionFingerprint() });
}
