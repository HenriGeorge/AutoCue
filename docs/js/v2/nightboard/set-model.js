/**
 * AutoCue 2.0 — Nightboard set model (P4).
 *
 * The in-memory set state + the fetch orchestration for the canvas. Pure state
 * + REST: no DOM, unit-testable in Vitest. The Nightboard interop contract
 * (R11) bans bare legacy-state reads (the parsed-track list, pending-cue map and
 * selection set) — those flow through window.ACBridge — but fetching the
 * analysis REST surface directly is allowed and is exactly this module's job.
 * Set construction stays in setbuilder.py; scoring stays in transitions.py;
 * this file never re-implements either.
 *
 * Joint↔score mapping: build_set returns each track dict with `transition_score`
 * = the score of the transition INTO that track (from the previous one). So the
 * joint between tile i and tile i+1 carries SET[i+1].transition_score, and a set
 * of N tiles has N−1 joints. rescoreJoints(idx) re-scores only the ≤2 joints
 * touching slot idx (R7) and writes the fresh `overall` back onto the incoming
 * track's `transition_score` so the canvas repaints from one source of truth.
 */

const SET = [];           // ordered SetBuilderTrackItem dicts
let _terminatedReason = null;
let _meta = { totalTracks: 0, estimatedDurationMinutes: 0 };
const _energy = new Map(); // track_id -> number[] (0..1 curve), cached across builds

export function getSet() { return SET; }
export function terminatedReason() { return _terminatedReason; }
export function meta() { return _meta; }
export function energyFor(id) { return _energy.get(Number(id)) || null; }

/**
 * Fetch every set track's energy curve in PARALLEL (the arc + tile sparklines
 * need them all). A failed/empty curve is simply absent — the canvas degrades
 * to a flat segment (R5), never a broken path. Cached, so re-builds and swaps
 * only fetch the genuinely-new ids.
 */
export async function loadEnergyCurves(ids) {
  const missing = [...new Set(ids.map(Number))].filter((id) => !_energy.has(id));
  await Promise.allSettled(missing.map(async (id) => {
    try {
      const r = await fetch(`/api/tracks/${id}/energy`);
      if (!r.ok) return;
      const d = await r.json();
      if (Array.isArray(d.energy) && d.energy.length) _energy.set(id, d.energy);
    } catch (_) { /* leave the curve absent → flat fallback */ }
  }));
}

/** Map canvas inputs → SetBuilderRequest, POST /api/setbuilder, parse response. */
export async function buildSet(cfg = {}) {
  const body = {
    start_bpm: Number(cfg.start_bpm) || 110,
    end_bpm: Number(cfg.end_bpm) || 135,
    duration_minutes: Number(cfg.duration_minutes) || 60,
    energy_mode: cfg.energy_mode || 'build',
  };
  if (Array.isArray(cfg.anchor_track_ids) && cfg.anchor_track_ids.length) {
    body.anchor_track_ids = cfg.anchor_track_ids;
  }
  const r = await fetch('/api/setbuilder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  const data = await r.json();
  SET.length = 0;
  for (const t of (data.tracks || [])) SET.push(t);
  _terminatedReason = data.terminated_reason || null;
  _meta = {
    totalTracks: data.total_tracks || SET.length,
    estimatedDurationMinutes: data.estimated_duration_minutes || 0,
  };
  return { tracks: SET, terminatedReason: _terminatedReason, ..._meta };
}

/** Replace the track at slot idx (swap-in). Caller rescores the touched joints. */
export function swapAt(idx, track) {
  if (idx < 0 || idx >= SET.length || !track) return false;
  SET[idx] = track;
  return true;
}

/** Insert a candidate after slot idx (tray Add). Caller rescores touched joints. */
export function insertAfter(idx, track) {
  if (idx < -1 || idx >= SET.length || !track) return false;
  SET.splice(idx + 1, 0, track);
  return true;
}

/**
 * Re-score ONLY the ≤2 joints touching slot idx via POST /api/transitions/score,
 * never a full rebuild (R7). Writes the fresh `overall` onto the incoming track's
 * `transition_score`. Returns the updated TransitionResponse dicts keyed by JOINT
 * index (joint j sits between SET[j] and SET[j+1]).
 */
export async function rescoreJoints(idx) {
  const pairs = [];
  if (idx - 1 >= 0) pairs.push([idx - 1, idx]);              // joint idx-1
  if (idx >= 0 && idx + 1 <= SET.length - 1) pairs.push([idx, idx + 1]); // joint idx
  const updated = {};
  for (const [ai, bi] of pairs) {
    const a = SET[ai], b = SET[bi];
    if (!a || !b || a.track_id === b.track_id) continue;
    try {
      const r = await fetch('/api/transitions/score', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_a_id: a.track_id, track_b_id: b.track_id }),
      });
      if (!r.ok) continue;
      const ts = await r.json();
      b.transition_score = ts.overall;     // incoming-edge score lives on the later track
      updated[ai] = ts;                    // joint index = ai (between ai and ai+1)
    } catch (_) { /* keep the prior score on transient failure */ }
  }
  return updated;
}

/** Test seam: reset module state between Vitest cases. */
export function _reset() {
  SET.length = 0;
  _terminatedReason = null;
  _meta = { totalTracks: 0, estimatedDurationMinutes: 0 };
  _energy.clear();
}
