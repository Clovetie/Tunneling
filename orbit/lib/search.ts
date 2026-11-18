/**
 * Cross-entity search scoring.
 *
 * Extracted from the overlay so ranking is testable on its own. The rule is
 * deliberately boring: every term must appear somewhere, then we reward early
 * matches, whole-word matches and multiplicity. No fuzzy matching, because a
 * confident wrong hit is worse than no hit.
 */

export interface Scorable {
  title: string;
  body: string;
}

export function parseTerms(query: string): string[] {
  return query
    .toLowerCase()
    .split(/\s+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 1);
}

export function scoreText(haystack: string, terms: string[]): number {
  if (!terms.length) return 0;
  const text = haystack.toLowerCase();
  let total = 0;
  let matched = 0;
  for (const term of terms) {
    const at = text.indexOf(term);
    if (at < 0) continue;
    matched += 1;
    // Earlier matches rank higher; position decays in 40-char buckets.
    total += 10 - Math.min(9, Math.floor(at / 40));
    if (at === 0) total += 8;
    let wholeWord = false;
    try {
      wholeWord = new RegExp(`\\b${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "i").test(haystack);
    } catch {
      wholeWord = false;
    }
    if (wholeWord) total += 5;
    const occurrences = text.split(term).length - 1;
    total += Math.min(3, occurrences - 1);
  }
  // Every term must hit, otherwise the result is noise.
  return matched === terms.length ? total + matched * 3 : 0;
}

export function scoreItem(item: Scorable, terms: string[]): number {
  if (!terms.length) return 0;
  const titleScore = scoreText(item.title, terms);
  const bodyScore = scoreText(item.body, terms);
  if (!titleScore && !bodyScore) return 0;
  // A title hit is worth materially more than a body hit.
  return Math.max(titleScore * 2, titleScore + bodyScore);
}
