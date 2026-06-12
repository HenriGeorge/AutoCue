/**
 * AutoCue 2.0 — fuzzy matcher (P1 T4). Pure, zero DOM.
 *
 * Subsequence match with bonuses for word-boundary hits and consecutive runs,
 * so "fd" → "Find duplicates" and "bs" → "Build set" rank above incidental
 * subsequence matches. Returns -1 for no match.
 */
export function fuzzyScore(query, text) {
  const q = String(query || '').toLowerCase().trim();
  const t = String(text || '').toLowerCase();
  if (!q) return 0; // empty query matches everything (score 0)
  if (!t) return -1;

  let score = 0;
  let qi = 0;
  let prevMatch = -2;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] !== q[qi]) continue;
    let pt = 1; // base point per matched char
    if (ti === prevMatch + 1) pt += 3; // consecutive run
    const boundary = ti === 0 || t[ti - 1] === ' ' || t[ti - 1] === '-' || t[ti - 1] === '/';
    if (boundary) pt += 5; // word-boundary hit
    if (ti === 0) pt += 2; // very start
    score += pt;
    prevMatch = ti;
    qi++;
  }
  if (qi < q.length) return -1; // not all query chars matched
  // Prefer shorter targets (a tie-breaker that keeps exact-ish matches on top).
  score -= Math.floor(t.length / 20);
  return score;
}

// Stable rank: keep input order for equal scores. textOf maps item → string.
export function rank(query, items, textOf) {
  const scored = items.map((item, i) => ({ item, i, s: fuzzyScore(query, textOf(item)) }));
  return scored
    .filter((x) => x.s >= 0)
    .sort((a, b) => (b.s - a.s) || (a.i - b.i))
    .map((x) => x.item);
}
