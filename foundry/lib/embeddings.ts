import { redactSecrets } from "@/lib/utils";

export type EmbeddingResult = {
  vector: number[] | null;
  mode: "vector" | "unavailable";
  provider: string | null;
  model: string | null;
  reason?: string;
};

export function toVectorLiteral(values: number[]): string {
  return `[${values.join(",")}]`;
}

export function embeddingStatus(): { configured: boolean; provider: string | null; model: string; dimensions: number } {
  const configured = Boolean(process.env.OPENAI_API_KEY);
  return {
    configured,
    provider: configured ? "openai" : null,
    model: process.env.OPENAI_EMBEDDING_MODEL || "text-embedding-3-small",
    dimensions: Number(process.env.FOUNDRY_EMBEDDING_DIMENSIONS || 1536)
  };
}

export async function embedText(text: string): Promise<EmbeddingResult> {
  const status = embeddingStatus();
  if (!status.configured) {
    return { vector: null, mode: "unavailable", provider: null, model: status.model, reason: "No embedding provider key is configured; lexical retrieval will be used." };
  }
  const base = (process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").replace(/\/$/, "");
  try {
    const response = await fetch(`${base}/embeddings`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
      body: JSON.stringify({ model: status.model, input: text.slice(0, 20_000), dimensions: status.dimensions }),
      signal: AbortSignal.timeout(30_000),
      cache: "no-store"
    });
    if (!response.ok) {
      const detail = redactSecrets((await response.text().catch(() => "")).slice(0, 300));
      throw new Error(`Embedding HTTP ${response.status}: ${detail}`);
    }
    const payload = await response.json() as { data?: Array<{ embedding?: number[] }> };
    const vector = payload.data?.[0]?.embedding ?? null;
    if (!vector || vector.length !== status.dimensions) throw new Error("Embedding provider returned an unexpected vector size");
    return { vector, mode: "vector", provider: status.provider, model: status.model };
  } catch (error) {
    return {
      vector: null, mode: "unavailable", provider: status.provider, model: status.model,
      reason: `Embedding unavailable (${String(redactSecrets(error instanceof Error ? error.message : String(error))).slice(0, 180)}); lexical retrieval will be used.`
    };
  }
}
