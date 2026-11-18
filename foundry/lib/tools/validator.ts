import Ajv, { type ErrorObject } from "ajv";

const ajv = new Ajv({ allErrors: true, strict: false, validateSchema: false, allowUnionTypes: true });

export type ValidationResult = { valid: boolean; errors: Array<{ path: string; message: string }> };

export function validateJsonSchema(schema: unknown, value: unknown): ValidationResult {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) return { valid: true, errors: [] };
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { valid: false, errors: [{ path: "", message: "Tool arguments must be an object" }] };
  }
  try {
    const validate = ajv.compile(schema as object);
    const valid = validate(value) as boolean;
    return {
      valid,
      errors: (validate.errors ?? []).map((error: ErrorObject) => ({ path: error.instancePath || "/", message: error.message ?? "invalid" }))
    };
  } catch (error) {
    return { valid: false, errors: [{ path: "/schema", message: error instanceof Error ? error.message : String(error) }] };
  }
}
