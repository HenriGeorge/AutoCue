"""Cue-placement accuracy benchmark.

Measures how closely AutoCue's automatic hot-cue placement matches a *reference*
set of cues, per track and in aggregate. The reference is either:

  • the DJ's own existing hot cues already in the Rekordbox library (the real
    human ground truth) — the default; or
  • a Rekordbox XML exported by another tool (e.g. Mixed In Key) via ``--mik``,
    so you can benchmark AutoCue head-to-head against MIK on the same tracks.

For each track we compute, at several beat-aware tolerance bands (≤1 beat,
≤1 bar, ≤2 bars):

  • recall    — fraction of reference cues AutoCue placed a cue near,
  • precision — fraction of AutoCue's cues that land near a reference cue,
  • f1,
  • median absolute offset (ms) of matched pairs.

Matching is one-to-one and time-based (slot letters are ignored — what matters
is whether a cue lands at the right *moment*).

CONTAMINATION NOTE: if the reference library was itself cued by AutoCue in the
past, "vs existing cues" is circular and will look inflated. The report counts
exact (0 ms) matches as a contamination signal — a high share means the
reference is largely AutoCue's own prior output, so use ``--mik`` (or a
human-cued subset) for an honest number.

Pure metric helpers (``match_cues`` / ``score_cues`` / ``beats_to_ms``) are
import-safe and unit-tested; the DB/XML loaders and CLI need a real library.

Run:  python -m autocue.bench.cue_accuracy --n 50
      python -m autocue.bench.cue_accuracy --mik mik_export.xml --n 50 --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field, asdict


# ── Pure metric helpers (unit-tested, no DB) ──────────────────────────────────

def beats_to_ms(beats: float, bpm: float) -> float:
    """Milliseconds spanned by `beats` at `bpm`. bpm<=0 → 0 (caller falls back)."""
    if bpm <= 0:
        return 0.0
    return beats * (60_000.0 / bpm)


def match_cues(reference_ms, predicted_ms, tol_ms):
    """One-to-one greedy nearest match between two cue-time lists (ms).

    Pairs every reference within `tol_ms` of a prediction, then assigns greedily
    by smallest absolute offset so no cue is matched twice. Returns a list of
    ``(ref_index, pred_index, signed_offset_ms)`` where offset = reference - pred.
    """
    if tol_ms < 0:
        tol_ms = 0
    candidates = []
    for i, r in enumerate(reference_ms):
        for j, p in enumerate(predicted_ms):
            d = r - p
            if abs(d) <= tol_ms:
                candidates.append((abs(d), i, j, d))
    candidates.sort(key=lambda c: c[0])
    used_ref, used_pred, matches = set(), set(), []
    for _, i, j, signed in candidates:
        if i in used_ref or j in used_pred:
            continue
        used_ref.add(i)
        used_pred.add(j)
        matches.append((i, j, signed))
    return matches


def score_cues(reference_ms, predicted_ms, tol_ms):
    """Precision / recall / f1 + offset stats for one track at one tolerance."""
    matches = match_cues(reference_ms, predicted_ms, tol_ms)
    n_ref, n_pred, n_match = len(reference_ms), len(predicted_ms), len(matches)
    precision = n_match / n_pred if n_pred else 0.0
    recall = n_match / n_ref if n_ref else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    abs_off = [abs(m[2]) for m in matches]
    exact = sum(1 for m in matches if m[2] == 0)
    return {
        "n_ref": n_ref,
        "n_pred": n_pred,
        "n_matched": n_match,
        "n_exact": exact,  # 0 ms matches — contamination signal
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_abs_offset_ms": statistics.fmean(abs_off) if abs_off else None,
        "median_abs_offset_ms": statistics.median(abs_off) if abs_off else None,
    }


# Tolerance bands, in beats. bpm<=0 falls back to the fixed-ms map below.
TOLERANCE_BANDS = {"<=1 beat": 1.0, "<=1 bar": 4.0, "<=2 bars": 8.0}
FALLBACK_MS = {"<=1 beat": 500.0, "<=1 bar": 2000.0, "<=2 bars": 4000.0}


def band_tolerances_ms(bpm: float) -> dict:
    """Resolve each band to a ms tolerance for this track's tempo."""
    if bpm and bpm > 0:
        return {k: beats_to_ms(v, bpm) for k, v in TOLERANCE_BANDS.items()}
    return dict(FALLBACK_MS)


# ── Per-track + aggregate evaluation (pure given parsed inputs) ────────────────

@dataclass
class TrackResult:
    track_id: str
    title: str
    artist: str
    bpm: float
    mode: str            # AutoCue mode: phrase | bar | heuristic | (none)
    bpm_known: bool
    bands: dict          # band -> score_cues(...) dict


@dataclass
class BenchResult:
    reference: str               # "existing" | "mik:<file>"
    n_tracks: int                # tracks evaluated (had >=1 ref and >=1 pred)
    n_requested: int
    n_skipped_no_ref: int
    n_skipped_no_pred: int
    by_mode: dict = field(default_factory=dict)
    aggregate: dict = field(default_factory=dict)
    tracks: list = field(default_factory=list)


def evaluate_track(track_id, title, artist, bpm, mode, reference_ms, predicted_ms):
    bpm_known = bool(bpm and bpm > 0)
    tols = band_tolerances_ms(bpm)
    bands = {band: score_cues(reference_ms, predicted_ms, tol) for band, tol in tols.items()}
    return TrackResult(str(track_id), title or "", artist or "", float(bpm or 0),
                       mode, bpm_known, bands)


def _macro(values):
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def aggregate_results(track_results):
    """Macro-average each band across tracks + a pooled offset/exact summary."""
    agg = {}
    for band in TOLERANCE_BANDS:
        prec = _macro([t.bands[band]["precision"] for t in track_results])
        rec = _macro([t.bands[band]["recall"] for t in track_results])
        f1 = _macro([t.bands[band]["f1"] for t in track_results])
        offs = [t.bands[band]["median_abs_offset_ms"] for t in track_results]
        agg[band] = {
            "precision": prec, "recall": rec, "f1": f1,
            "median_offset_ms": _macro(offs),
        }
    # Contamination signal at the loosest band (most matches).
    loose = "<=2 bars"
    tot_match = sum(t.bands[loose]["n_matched"] for t in track_results)
    tot_exact = sum(t.bands[loose]["n_exact"] for t in track_results)
    agg["_exact_match_share"] = (tot_exact / tot_match) if tot_match else None
    agg["_total_matched"] = tot_match
    agg["_total_exact"] = tot_exact
    return agg


def summarize_by_mode(track_results):
    by_mode = {}
    modes = sorted({t.mode for t in track_results})
    for m in modes:
        subset = [t for t in track_results if t.mode == m]
        by_mode[m] = {
            "n": len(subset),
            "recall_1bar": _macro([t.bands["<=1 bar"]["recall"] for t in subset]),
            "precision_1bar": _macro([t.bands["<=1 bar"]["precision"] for t in subset]),
            "f1_1bar": _macro([t.bands["<=1 bar"]["f1"] for t in subset]),
        }
    return by_mode


# ── Reference + prediction loaders (need a real library) ──────────────────────

def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def load_existing_hot_cues(db):
    """{content_id(str): sorted [InMsec,...]} for hot cues (Kind 1-8). One query."""
    from pyrekordbox.db6 import DjmdCue
    rows = (
        db.query(DjmdCue.ContentID, DjmdCue.InMsec)
        .filter(DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
        .all()
    )
    out = {}
    for cid, ms in rows:
        if ms is None:
            continue
        out.setdefault(str(cid), []).append(float(ms))
    for cid in out:
        out[cid].sort()
    return out


def load_mik_xml(path):
    """Parse a Rekordbox XML (e.g. Mixed In Key export) → {(artist,title)norm:
    sorted [ms,...]} from POSITION_MARK Start (seconds → ms). Hot cues only
    (Num >= 0)."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    out = {}
    for tr in root.iter("TRACK"):
        # COLLECTION tracks carry Name/Artist + POSITION_MARKs; playlist refs don't.
        name = tr.get("Name")
        if name is None:
            continue
        key = (_norm(tr.get("Artist")), _norm(name))
        marks = []
        for pm in tr.findall("POSITION_MARK"):
            try:
                num = int(pm.get("Num", "-1"))
            except ValueError:
                num = -1
            if num < 0:
                continue  # memory cue
            try:
                marks.append(float(pm.get("Start", "0")) * 1000.0)
            except ValueError:
                continue
        if marks:
            out[key] = sorted(marks)
    return out


def predict_cues_ms(content, db):
    """Run AutoCue → (sorted [position_ms,...] for hot cues only, mode)."""
    from autocue.generator import generate_cues_for_track

    cues, mode = generate_cues_for_track(content, db, None)
    ms = sorted(float(c.position_ms) for c in cues if c.slot is not None and c.slot >= 0)
    return ms, mode


def _bpm_of(content) -> float:
    raw = getattr(content, "BPM", None)
    try:
        v = float(raw) / 100.0 if raw else 0.0
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else 0.0


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_benchmark(db, *, n=50, seed=0, mik_xml=None, phrase_only=False):
    """Evaluate up to `n` sampled tracks. Deterministic given `seed`."""
    import random

    rng = random.Random(seed)
    ref_by_track = load_existing_hot_cues(db)
    mik_map = load_mik_xml(mik_xml) if mik_xml else None
    reference_label = f"mik:{mik_xml}" if mik_xml else "existing"

    candidate_ids = sorted(ref_by_track.keys()) if not mik_xml else None

    if mik_xml:
        # Sample from all tracks; keep those present in the MIK export.
        all_content = list(db.get_content().all())
        rng.shuffle(all_content)
        contents = all_content
    else:
        ids = list(candidate_ids)
        rng.shuffle(ids)
        contents = (db.get_content(ID=i) for i in ids)

    results, n_no_ref, n_no_pred = [], 0, 0
    for content in contents:
        if content is None:
            continue
        cid = str(getattr(content, "ID", ""))
        title = getattr(content, "Title", "") or ""
        artist = getattr(content, "ArtistName", "") or ""

        if mik_xml:
            reference_ms = mik_map.get((_norm(artist), _norm(title)))
            if not reference_ms:
                continue  # not in the MIK export
        else:
            reference_ms = ref_by_track.get(cid)
            if not reference_ms:
                n_no_ref += 1
                continue

        predicted_ms, mode = predict_cues_ms(content, db)
        if phrase_only and mode != "phrase":
            continue
        if not predicted_ms:
            n_no_pred += 1
            continue

        results.append(evaluate_track(cid, title, artist, _bpm_of(content),
                                      mode, reference_ms, predicted_ms))
        if len(results) >= n:
            break

    return BenchResult(
        reference=reference_label,
        n_tracks=len(results),
        n_requested=n,
        n_skipped_no_ref=n_no_ref,
        n_skipped_no_pred=n_no_pred,
        by_mode=summarize_by_mode(results),
        aggregate=aggregate_results(results),
        tracks=[asdict(t) for t in results],
    )


def _pct(x):
    return "  —  " if x is None else f"{x * 100:5.1f}%"


def _ms(x):
    return "   —  " if x is None else f"{x:5.0f}ms"


def format_report(res: BenchResult) -> str:
    L = []
    L.append("=" * 64)
    L.append("AutoCue — cue placement accuracy")
    L.append("=" * 64)
    L.append(f"Reference : {res.reference}")
    L.append(f"Evaluated : {res.n_tracks} tracks (requested {res.n_requested})")
    if res.n_skipped_no_ref or res.n_skipped_no_pred:
        L.append(f"Skipped   : {res.n_skipped_no_ref} no-reference, "
                 f"{res.n_skipped_no_pred} no-AutoCue-cues")
    if res.n_tracks == 0:
        L.append("\nNo tracks evaluated — check the library / MIK export matches.")
        return "\n".join(L)

    L.append("")
    L.append(f"{'Tolerance':<12}{'Recall':>9}{'Precision':>11}{'F1':>9}{'MedOffset':>11}")
    L.append("-" * 52)
    for band in TOLERANCE_BANDS:
        a = res.aggregate[band]
        L.append(f"{band:<12}{_pct(a['recall']):>9}{_pct(a['precision']):>11}"
                 f"{_pct(a['f1']):>9}{_ms(a['median_offset_ms']):>11}")

    L.append("")
    L.append("By AutoCue mode (at <=1 bar):")
    for mode, m in sorted(res.by_mode.items()):
        L.append(f"  {mode:<10} n={m['n']:<4} recall {_pct(m['recall_1bar'])}"
                 f"  precision {_pct(m['precision_1bar'])}  f1 {_pct(m['f1_1bar'])}")

    share = res.aggregate.get("_exact_match_share")
    if share is not None:
        L.append("")
        L.append(f"Exact (0 ms) matches: {res.aggregate['_total_exact']}/"
                 f"{res.aggregate['_total_matched']} = {share * 100:.0f}%")
        if share >= 0.30 and res.reference == "existing":
            L.append("  ⚠ High exact-match share — many reference cues look like")
            L.append("    AutoCue's OWN prior output. This number is inflated;")
            L.append("    re-run against --mik or a human-cued library for an honest result.")
    L.append("=" * 64)
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="Benchmark AutoCue cue placement vs a reference.")
    p.add_argument("--n", type=int, default=50, help="tracks to evaluate (default 50)")
    p.add_argument("--seed", type=int, default=0, help="sampling seed (deterministic)")
    p.add_argument("--mik", metavar="XML",
                   help="Rekordbox XML exported by Mixed In Key — use as the reference")
    p.add_argument("--phrase-only", action="store_true",
                   help="only evaluate tracks AutoCue cues in phrase mode")
    p.add_argument("--db-path", help="path to master.db (auto-detected on macOS)")
    p.add_argument("--json", metavar="FILE", help="also write the full result as JSON")
    args = p.parse_args(argv)

    try:
        from pyrekordbox import Rekordbox6Database as MasterDatabase
    except ImportError:  # pragma: no cover
        from pyrekordbox import MasterDatabase  # type: ignore
    db = MasterDatabase(args.db_path) if args.db_path else MasterDatabase()

    res = run_benchmark(db, n=args.n, seed=args.seed, mik_xml=args.mik,
                        phrase_only=args.phrase_only)
    print(format_report(res))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(asdict(res), f, indent=2)
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
