export type PublicConfig = {
  appName: "FOUNDRY";
  version: string;
  supabaseUrl: string;
  authConfigured: boolean;
  databaseConfigured: boolean;
  realtimeConfigured: boolean;
  encryptionConfigured: boolean;
  workerConfigured: boolean;
  bridgeBootstrapConfigured: boolean;
  bridgePreviewEnabled: boolean;
  providers: string[];
  mode: "unconfigured" | "partial" | "ready";
  warnings: string[];
};

function has(value: string | undefined | null): boolean {
  return Boolean(value && value.trim());
}

export function getPublicConfig(): PublicConfig {
  const authConfigured = has(process.env.NEXT_PUBLIC_SUPABASE_URL) && has(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);
  const databaseConfigured = has(process.env.DATABASE_URL) || has(process.env.SUPABASE_DB_URL);
  const realtimeConfigured = authConfigured && databaseConfigured;
  const encryptionConfigured = has(process.env.FOUNDRY_SECRET_ENCRYPTION_KEY);
  const workerConfigured = process.env.FOUNDRY_WORKER_MODE === "true" || has(process.env.FOUNDRY_WORKER_ID);
  const bridgeBootstrapConfigured = has(process.env.FOUNDRY_TUNNELING_URL) && has(process.env.FOUNDRY_TUNNELING_TOKEN);
  const bridgePreviewEnabled = bridgeBootstrapConfigured && (process.env.NODE_ENV !== "production" || process.env.FOUNDRY_PUBLIC_BRIDGE_PREVIEW === "true");
  const providers: string[] = [];
  if (has(process.env.OPENAI_API_KEY)) providers.push("openai");
  if (has(process.env.ANTHROPIC_API_KEY)) providers.push("anthropic");
  if (has(process.env.GOOGLE_GENERATIVE_AI_API_KEY)) providers.push("google");
  const warnings: string[] = [];
  if (!authConfigured) warnings.push("Supabase Auth is not configured; sign-in and all database operations are disabled.");
  if (!databaseConfigured) warnings.push("DATABASE_URL is not configured; no persistent records will be created.");
  if (!encryptionConfigured) warnings.push("FOUNDRY_SECRET_ENCRYPTION_KEY is not configured; integration secrets cannot be stored.");
  if (!workerConfigured) warnings.push("No persistent worker heartbeat is configured; durable execution will remain queued.");
  if (providers.length === 0) warnings.push("No model provider key is configured; model-backed tasks will fail honestly.");
  const mode = authConfigured && databaseConfigured && encryptionConfigured ? "ready" : (!authConfigured && !databaseConfigured ? "unconfigured" : "partial");
  return {
    appName: "FOUNDRY",
    version: "0.1.0",
    supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    authConfigured,
    databaseConfigured,
    realtimeConfigured,
    encryptionConfigured,
    workerConfigured,
    bridgeBootstrapConfigured,
    bridgePreviewEnabled,
    providers,
    mode,
    warnings
  };
}
