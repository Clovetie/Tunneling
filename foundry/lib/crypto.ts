import crypto from "node:crypto";

const KEY_ENV = "FOUNDRY_SECRET_ENCRYPTION_KEY";
const KEY_VERSION = 1;

export function isEncryptionConfigured(): boolean {
  return Boolean(process.env[KEY_ENV]?.trim());
}

function getKey(): Buffer {
  const raw = process.env[KEY_ENV]?.trim();
  if (!raw) throw new Error("FOUNDRY_SECRET_ENCRYPTION_KEY is not configured");
  const candidates: Buffer[] = [];
  if (/^[0-9a-fA-F]{64}$/.test(raw)) candidates.push(Buffer.from(raw, "hex"));
  try { candidates.push(Buffer.from(raw, "base64")); } catch { /* ignore */ }
  const key = candidates.find((candidate) => candidate.length === 32);
  if (!key) throw new Error("FOUNDRY_SECRET_ENCRYPTION_KEY must be 32 bytes encoded as base64 or 64 hex characters");
  return key;
}

export type EncryptedSecret = {
  ciphertext: string;
  iv: string;
  authTag: string;
  keyVersion: number;
};

export function encryptSecret(plaintext: string): EncryptedSecret {
  if (typeof plaintext !== "string" || !plaintext) throw new Error("Cannot encrypt an empty secret");
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", getKey(), iv);
  const encrypted = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return {
    ciphertext: encrypted.toString("base64"),
    iv: iv.toString("base64"),
    authTag: cipher.getAuthTag().toString("base64"),
    keyVersion: KEY_VERSION
  };
}

export function decryptSecret(record: { ciphertext: string; iv: string; auth_tag?: string; authTag?: string; key_version?: number; keyVersion?: number }): string {
  const authTag = record.auth_tag ?? record.authTag;
  if (!authTag) throw new Error("Encrypted secret is missing its authentication tag");
  if ((record.key_version ?? record.keyVersion ?? KEY_VERSION) !== KEY_VERSION) throw new Error("Unsupported secret key version");
  const decipher = crypto.createDecipheriv("aes-256-gcm", getKey(), Buffer.from(record.iv, "base64"));
  decipher.setAuthTag(Buffer.from(authTag, "base64"));
  return Buffer.concat([decipher.update(Buffer.from(record.ciphertext, "base64")), decipher.final()]).toString("utf8");
}

export function encryptionFingerprint(): string {
  if (!isEncryptionConfigured()) return "";
  return crypto.createHash("sha256").update(getKey()).digest("hex").slice(0, 12);
}
