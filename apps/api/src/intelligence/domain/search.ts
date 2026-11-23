/**
 * Transcript search box → Postgres tsquery. Every word must match (AND), each as a prefix, so
 * "black hol" finds "black holes". Only letters and digits survive, so the result is safe to
 * pass to to_tsquery; null when nothing searchable is left.
 */
export function prefixTsQuery(input: string, maxTerms = 8): string | null {
  const words = (input.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).slice(0, maxTerms);
  return words.length ? words.map((w) => `${w}:*`).join(' & ') : null;
}
