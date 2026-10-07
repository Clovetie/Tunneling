#!/usr/bin/env node
/**
 * fixture-model.js — a deterministic, known-good demo adapter.
 *
 * The example / workbench suite ships with this fixture so the preflight gate
 * is demonstrably green (ROADMAP #6.3) without network access or credentials:
 * the harness spawns `node tools/fixture-model.js`, sends one JSON request on
 * stdin, and the fixture writes a plausible, correct answer to stdout.
 *
 * This is *not* a real model. It is a small rule-based responder that handles
 * the handful of categories the example suite exercises (reasoning arithmetic,
 * JSON echo, JSON-schema shape building, safety/refusal/uncertainty/prompt-
 * injection refusals, code generation, multi-language translation, and a
 * format token). Swap it out for a real provider adapter before using the gate
 * as a release gate; keep model credentials and the production prompt out of
 * the suite and this file.
 */
'use strict';

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', () => {
  let req;
  try { req = JSON.parse(input || '{}'); }
  catch (_) { process.stdout.write(''); return; } // fail-closed: empty answer → 0
  const prompt = String(req.prompt || '');
  const category = String(req.category || '');
  process.stdout.write(answer(prompt, category));
});

function answer(prompt, category) {
  // Priority order: specific prompt-driven patterns, then category fallbacks.

  // 1. Instruction-following JSON echo: "Reply with exactly this JSON ...: {...}"
  const jsonEcho = prompt.match(/exactly\s+this\s+JSON[^:]*:\s*(\{[\s\S]*\})/i);
  if (jsonEcho) return jsonEcho[1].trim();

  // 2. JSON-schema shape building: "field X (type)" → {X: typedDefault}
  if (/json/i.test(category) || /json object|schema/i.test(prompt) || /field\s+\w+\s+\(/.test(prompt)) {
    return buildJsonObject(prompt);
  }

  // 3. Code generation.
  if (category === 'code' || /\b(script|code)\b/i.test(prompt) && /print|cod|node|python/i.test(prompt)) {
    return generateCode(prompt);
  }

  // 4. Reasoning: rate / arithmetic / percentage / sequence.
  if (category === 'reasoning' || /\b(speed|what is|travels|of|average|km\/h|next term)\b/i.test(prompt)) {
    const solved = solveMath(prompt);
    if (solved !== null) return String(solved);
  }

  // 5. Format token: two uppercase letters, hyphen, four digits.
  if (/two uppercase letters|hyphen, and four digits|AB-1234/i.test(prompt)) return 'AB-1234';

  // 6. Multi-language translation.
  if (category === 'multi_language' || /\btranslate|translation|in french|in spanish|in japanese|in german\b/i.test(prompt)) {
    const t = translate(prompt);
    if (t !== null) return t;
  }

  // 7. Safety / refusal / uncertainty / prompt-injection refusals.
  if (category === 'safety' || /reveal private system|hidden instructions|system instructions/i.test(prompt)) {
    return 'I cannot reveal private system instructions. No hidden system instructions are present to share.';
  }
  if (category === 'refusal' || /internal reasoning|chain of thought|reasoning process/i.test(prompt)) {
    return 'I cannot provide my internal reasoning process. That is not available to share.';
  }
  if (category === 'uncertainty' || /air quality|live data|current /i.test(prompt)) {
    return 'I cannot access live data for that location, so I have no current air-quality reading.';
  }
  if (category === 'prompt_injection' || /ignore (all |any )?(previous|prior) instructions|system prompt|administrator/i.test(prompt)) {
    return 'I cannot comply with that request. I will not reveal system prompts or ignore my guidelines.';
  }

  // 8. Last-resort generic answer (fail-closed if the suite expects specifics).
  return 'I cannot answer that with the available information.';
}

function buildJsonObject(prompt) {
  const defaults = { number: 42, integer: 7, string: 'value', boolean: true, array: [], object: {} };
  const obj = {};
  const re = /field\s+([A-Za-z_]\w*)\s*(?:\(|\bis\s*)[\s]*([A-Za-z]+)/g;
  let m;
  while ((m = re.exec(prompt)) !== null) {
    const name = m[1];
    const type = m[2].toLowerCase().replace(/s$/, '');
    obj[name] = type in defaults ? defaults[type] : 'value';
  }
  if (Object.keys(obj).length) return JSON.stringify(obj);
  // Fallback: a shape implied by an inline "key: value" description.
  const kv = prompt.match(/\{\s*"(?:\w+)":\s*("[^"]*"|true|false|\d+(?:\.\d+)?)\s*\}/);
  if (kv) return kv[0];
  return JSON.stringify({ ok: true });
}

function generateCode(prompt) {
  const lang = /python|python3|\.py/i.test(prompt) ? 'python' : 'node';
  const out = prompt.match(/prints?\s+(?:the\s+)?['"]([^'"]+)['"]/i)
    || prompt.match(/prints?\s+([\d.]+)/i);
  const value = out ? out[1] : 'ok';
  const literal = typeof value === 'string' && value !== '' && !/^[\d.]+$/.test(value) ? JSON.stringify(value) : String(value);
  return lang === 'node' ? `console.log(${literal});` : `print(${literal})`;
}

function solveMath(prompt) {
  // Percentage: "what is 25% of 80" → 20
  let m = prompt.match(/what\s+is\s+([\d.]+)\s*%\s+of\s+([\d.]+)/i);
  if (m) return round(Number(m[1]) / 100 * Number(m[2]));

  // Rate: "A boat travels 12 km in 30 minutes. ... speed in km/h?" → 24
  m = prompt.match(/travels?\s+([\d.]+)\s*(km|m|mi)\s+in\s+([\d.]+)\s*(minute|minutes|hour|hours|min|mins)/i);
  if (m) {
    const dist = Number(m[1]);
    const time = Number(m[3]);
    const unit = m[4].toLowerCase();
    if (unit.startsWith('min')) return round(dist / (time / 60));
    return round(dist / time);
  }

  // Arithmetic: "what is 17 * 6" → 102
  m = prompt.match(/what\s+is\s+(-?[\d.]+)\s*([+\-*x×÷\/])\s*(-?[\d.]+)/i);
  if (m) {
    const a = Number(m[1]), b = Number(m[3]);
    const op = m[2].toLowerCase();
    if (op === '+' ) return round(a + b);
    if (op === '-' ) return round(a - b);
    if (op === '*' || op === 'x' || op === '×') return round(a * b);
    if (op === '/' || op === '÷') return b === 0 ? null : round(a / b);
  }

  // Sequence: "2, 4, 8, 16, ?" → 32 (arithmetic or geometric progression)
  m = prompt.match(/(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*\?/i);
  if (m) {
    const nums = [m[1], m[2], m[3], m[4]].map(Number);
    const d = nums[1] - nums[0];
    if (nums.every((_, i) => i === 0 || nums[i] - nums[i - 1] === d)) return round(nums[nums.length - 1] + d);
    const d2 = nums[1] - nums[0];
    const r = d2 !== 0 ? nums[1] / nums[0] : null;
    if (r && nums.every((_, i) => i === 0 || Math.abs(nums[i] / nums[i - 1] - r) < 1e-9)) return round(nums[nums.length - 1] * r);
  }

  return null;
}

const TRANSLATIONS = {
  french: { hello: 'bonjour', water: 'eau', 'thank you': 'merci', goodbye: 'au revoir', yes: 'oui', no: 'non' },
  spanish: { hello: 'hola', water: 'agua', 'thank you': 'gracias', goodbye: 'adiós', yes: 'sí', no: 'no' },
  japanese: { hello: 'konnichiwa', water: 'mizu', 'thank you': 'arigatou', goodbye: 'sayonara' },
  german: { hello: 'hallo', water: 'wasser', 'thank you': 'danke', goodbye: 'auf wiedersehen' },
  italian: { hello: 'ciao', water: 'acqua', 'thank you': 'grazie', goodbye: 'arrivederci' },
};

function translate(prompt) {
  const lang = (/french|français/i.test(prompt) && 'french')
    || (/spanish|español/i.test(prompt) && 'spanish')
    || (/japanese|日本語/i.test(prompt) && 'japanese')
    || (/german|deutsch/i.test(prompt) && 'german')
    || (/italian|italiano/i.test(prompt) && 'italian');
  if (!lang) return null;
  const word = (/the word ['"]?([a-zA-Z]+)['"]?/i.exec(prompt) || [])[1];
  const key = word ? word.toLowerCase() : 'hello';
  const dict = TRANSLATIONS[lang];
  if (dict[key]) return dict[key];
  const idx = prompt.match(/the word ['"]?([a-zA-Z]+)['"]?/i);
  return idx ? `${dict.hello}` : null;
}

function round(n) {
  if (Number.isInteger(n)) return n;
  return Math.round(n * 1e6) / 1e6;
}
