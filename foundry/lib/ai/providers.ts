import { generateText, streamText, type LanguageModel } from "ai";
import { redactSecrets } from "@/lib/utils";

export type ModelSpec = {
  provider: string;
  model: string;
  temperature?: number;
  maxTokens?: number;
  topP?: number;
  timeoutMs?: number;
};

export function configuredProviders(): string[] {
  const providers: string[] = [];
  if (process.env.OPENAI_API_KEY) providers.push("openai");
  if (process.env.ANTHROPIC_API_KEY) providers.push("anthropic");
  if (process.env.GOOGLE_GENERATIVE_AI_API_KEY) providers.push("google");
  if (process.env.OPENAI_COMPATIBLE_API_KEY && process.env.OPENAI_COMPATIBLE_BASE_URL) providers.push("openai_compatible");
  return providers;
}

export async function loadLanguageModel(spec: ModelSpec): Promise<LanguageModel> {
  const provider = (spec.provider || "openai").toLowerCase();
  const model = spec.model || "gpt-5-mini";
  if (provider === "openai") {
    if (!process.env.OPENAI_API_KEY) throw new Error("MODEL_PROVIDER_UNAVAILABLE: OPENAI_API_KEY is not configured");
    const { createOpenAI } = await import("@ai-sdk/openai");
    return createOpenAI({ apiKey: process.env.OPENAI_API_KEY, baseURL: process.env.OPENAI_BASE_URL || undefined })(model);
  }
  if (provider === "anthropic") {
    if (!process.env.ANTHROPIC_API_KEY) throw new Error("MODEL_PROVIDER_UNAVAILABLE: ANTHROPIC_API_KEY is not configured");
    const { createAnthropic } = await import("@ai-sdk/anthropic");
    return createAnthropic({ apiKey: process.env.ANTHROPIC_API_KEY })(model);
  }
  if (provider === "google" || provider === "google-generative-ai") {
    if (!process.env.GOOGLE_GENERATIVE_AI_API_KEY) throw new Error("MODEL_PROVIDER_UNAVAILABLE: GOOGLE_GENERATIVE_AI_API_KEY is not configured");
    const { createGoogleGenerativeAI } = await import("@ai-sdk/google");
    return createGoogleGenerativeAI({ apiKey: process.env.GOOGLE_GENERATIVE_AI_API_KEY })(model);
  }
  if (provider === "openai_compatible") {
    if (!process.env.OPENAI_COMPATIBLE_API_KEY || !process.env.OPENAI_COMPATIBLE_BASE_URL) throw new Error("MODEL_PROVIDER_UNAVAILABLE: OpenAI-compatible provider is not configured");
    const { createOpenAI } = await import("@ai-sdk/openai");
    return createOpenAI({ apiKey: process.env.OPENAI_COMPATIBLE_API_KEY, baseURL: process.env.OPENAI_COMPATIBLE_BASE_URL })(model);
  }
  throw new Error(`MODEL_PROVIDER_UNAVAILABLE: unsupported provider ${provider}`);
}

export type GenerationResult = {
  text: string;
  usage: { inputTokens?: number; outputTokens?: number; totalTokens?: number; [key: string]: unknown };
  finishReason?: string;
  provider: string;
  model: string;
};

function scrubError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return String(redactSecrets(message)).slice(0, 700);
}

export async function runModelText(input: {
  spec: ModelSpec;
  system: string;
  prompt: string;
  onChunk?: (delta: string, accumulated: string) => Promise<void> | void;
}): Promise<GenerationResult> {
  let model: LanguageModel;
  try { model = await loadLanguageModel(input.spec); }
  catch (error) { throw new Error(scrubError(error)); }
  const timeoutMs = input.spec.timeoutMs ?? 120_000;
  try {
    const result = streamText({
      model,
      system: input.system,
      prompt: input.prompt,
      temperature: input.spec.temperature,
      maxOutputTokens: input.spec.maxTokens,
      topP: input.spec.topP,
      abortSignal: AbortSignal.timeout(timeoutMs)
    });
    let text = "";
    for await (const delta of result.textStream) {
      text += delta;
      if (input.onChunk) await input.onChunk(delta, text);
    }
    const usage = await result.totalUsage;
    const finishReason = await result.finishReason.catch(() => undefined);
    return {
      text,
      usage: {
        inputTokens: usage?.inputTokens ?? undefined,
        outputTokens: usage?.outputTokens ?? undefined,
        totalTokens: usage?.totalTokens ?? undefined,
        ...((usage as Record<string, unknown> | undefined) ?? {})
      },
      finishReason: finishReason ? String(finishReason) : undefined,
      provider: input.spec.provider,
      model: input.spec.model
    };
  } catch (error) {
    throw new Error(`MODEL_EXECUTION_FAILED: ${scrubError(error)}`);
  }
}

export async function runModelJson<T>(input: { spec: ModelSpec; system: string; prompt: string; timeoutMs?: number }): Promise<{ value: T; text: string; usage: GenerationResult["usage"] }> {
  let model: LanguageModel;
  try { model = await loadLanguageModel(input.spec); }
  catch (error) { throw new Error(scrubError(error)); }
  try {
    const result = await generateText({
      model,
      system: input.system,
      prompt: input.prompt,
      temperature: input.spec.temperature ?? 0,
      maxOutputTokens: input.spec.maxTokens ?? 6000,
      abortSignal: AbortSignal.timeout(input.timeoutMs ?? input.spec.timeoutMs ?? 120_000)
    });
    const text = result.text.trim();
    const jsonStart = text.indexOf("{");
    const jsonEnd = text.lastIndexOf("}");
    if (jsonStart < 0 || jsonEnd <= jsonStart) throw new Error("Model did not return a JSON object");
    const value = JSON.parse(text.slice(jsonStart, jsonEnd + 1)) as T;
    const usage = result.totalUsage ?? result.usage;
    return {
      value,
      text,
      usage: {
        inputTokens: usage?.inputTokens ?? undefined,
        outputTokens: usage?.outputTokens ?? undefined,
        totalTokens: usage?.totalTokens ?? undefined
      }
    };
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("MODEL_PLAN_INVALID_JSON");
    throw new Error(`MODEL_EXECUTION_FAILED: ${scrubError(error)}`);
  }
}
