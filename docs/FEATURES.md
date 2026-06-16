# AutoCue Feature Documentation

## Overview

AutoCue gives Rekordbox 6 and 7 users three ways to automate hot cue placement and analyse their library:

**CLI** (`autocue --library`) — reads the Rekordbox database and ANLZ analysis files directly from disk, applies its phrase → bar → heuristic placement strategy, and writes a Rekordbox XML file for import. No internet connection. No server.

**Local server + web app** (`autocue serve`) — starts a FastAPI server at `http://localhost:7432` and opens the bundled web UI in your browser. The server reads and writes the Rekordbox `master.db` database directly, so there is no XML export/import step. All intelligence features (energy, mixability, classification, similar tracks, transition scoring, set builder, library health, auto-tagging, comment enrichment, Discogs genre tags, new-release discovery, and YouTube download) are only available in this mode.

**Hosted web app** (`docs/index.html`, served from GitHub Pages) — runs entirely in your browser with no installation. Upload a Rekordbox XML, configure settings, download a new XML with cues injected. Phrase analysis is supported if you also drop in your Rekordbox `share/` folder containing the ANLZ files.

---

## Feature 1: Smart Cue Placement

### How it works

AutoCue reads Rekordbox's own analysis data — the same files that power Rekordbox's phrase detector and waveform display — and uses it to place cues at musically meaningful positions. When that data is not available it falls back to progressively simpler strategies so that every track in your library gets cues regardless of analysis state.

### Three placement strategies

**Phrase-based (confidence 1.0)**

Requires that Rekordbox has run "Phrase Analysis" on the track, which writes a `.EXT` ANLZ file alongside the track. AutoCue reads two binary structures from these files:

- `PSSI` tag (from `.EXT`) — phrase boundary list. Each entry has a beat number and a `kind` code that AutoCue maps to a DJ-friendly label: Intro, Verse, Drop (Chorus), Build (Up), Break (Down), Bridge, Outro.
- `PQTZ` tag (from `.DAT`) — beat grid. A list of beat timestamps in milliseconds, one per beat, from which AutoCue converts beat numbers to exact millisecond positions.

The parser uses a two-pass algorithm. Pass 1 ensures every unique phrase label (Intro, Drop, Outro, etc.) gets at least one slot even when the track has many repeating sections. Pass 2 fills remaining slots up to 8 total with remaining phrases in chronological order. Duplicate timestamps from degenerate PSSI data are deduplicated before assignment.

If Rekordbox 6 exported the file, the PSSI tag data may be XOR-garbled. AutoCue detects this by reading the `mood` field and applies the correct unmasking key when the raw value is out of range. If pyrekordbox raises a `ConstError` (e.g. on Rekordbox 7's newer `PQT2` tag version), AutoCue falls back to a byte-level resilient scanner that skips the problematic tag and recovers whatever it can from the file.

**Bar-interval (confidence 0.6)**

Used when BPM is known but phrase analysis is absent. The formula is:

```
bar_duration_ms = (60,000 / BPM) × 4
cue_position[i] = inizio_ms + (start_bar − 1 + i × bars_interval) × bar_duration_ms
```

`inizio_ms` is the first-beat offset stored in the Rekordbox XML. The default places cues at bars 1, 17, 33, 49, 65, 81, 97, 113 (8 cues every 16 bars). Both `bars_interval` and `start_bar` are configurable.

**Heuristic fallback (confidence 0.3)**

Used only when BPM is also unavailable. Places one cue every 30 seconds, named `0:00`, `0:30`, `1:00`, … up to `max_cues` within the track's known duration. This is a last resort that at least gives CDJ users load points.

### Smart slot ordering

When phrase mode is used, AutoCue does not assign slots sequentially. Instead it applies a priority model designed for DJ workflow:

- **Slot A (hot cue 1)** — always the first non-Intro phrase in the track. This is the point you would press to start the track during a live transition. It is renamed to append "(Mix In)" so you can see it at a glance on a CDJ.
- **Slot B (hot cue 2)** — hard-reserved for the **first Outro** phrase (the mix-out window), when the track has one. It is renamed to append "(Outro)". If there is no Outro phrase, Slot B is filled from the priority list below.
- **Slots C–H** — remaining phrases sorted by musical importance: Drop (0) → Build (1) → Outro (2) → Verse (3) → Break (4) → Bridge (5) → Intro (6). Within each priority tier, phrases are ordered chronologically.

This means pressing Slot A on a CDJ always starts you at the mix-in point and Slot B is always the safe mix-out (first Outro) — regardless of the order in which phrases appear in the track.

### Memory cue (CDJ Auto Cue)

A memory cue (Rekordbox `Kind=0`, not a hot cue) controls where the CDJ Auto Cue function positions the playhead when a track is loaded from USB. AutoCue supports three memory cue modes, selectable in the UI:

| Mode | What is placed |
|---|---|
| **None** | No memory cues |
| **Load point** | One memory cue at the mix-in position (earliest phrase, or `inizio_ms` in bar/heuristic mode) |
| **All points** | Load point + Mix In (slot-A cue position, if different from load) + Mix Out (last Outro phrase) + Warning (16 bars before track end, only when no Outro or Outro is short) |

Memory cues in "All points" mode are only fully generated in phrase mode because Mix Out requires an Outro phrase and the Warning cue requires a BPM-accurate bar calculation.

### Confidence scoring

Every generated `CuePoint` carries a `confidence` field:

- `1.0` — phrase mode: position derived from Rekordbox's beat-accurate analysis
- `0.6` — bar mode: position calculated from BPM (accurate to within a bar's rounding error)
- `0.3` — heuristic: rough 30-second estimate

The confidence value is stored on the cue object and returned to the UI so cue badges can display `High`, `Medium`, or `Low`.

### Using it

**CLI:**
```bash
autocue --library                  # all tracks, auto strategy
autocue --track "Song Title"       # single track by title
autocue --track-id 42              # single track by Rekordbox ID
autocue --library --dry-run        # preview without writing
autocue --library --overwrite      # re-generate even for tracks that already have cues
```

**Local server UI:** Select a playlist or work on all tracks. Choose mode (`auto` or `bar`). Click "Preview cues" (F5) to see cue positions before committing, then "Apply to Rekordbox" to write. Rekordbox must be closed before applying.

**API:** `POST /api/generate` previews; `POST /api/generate-apply-stream` generates and writes in one SSE-streamed pass.

---

## Feature 2: Track Energy Analysis

### What the energy curve is

Every track in your Rekordbox library that has been analysed has a waveform overview stored in the `.DAT` ANLZ file as a `PWAV` tag. This is the same miniature waveform strip you see in Rekordbox's browser column. AutoCue reads this tag to extract a normalized energy curve — a sequence of floating-point values between 0.0 and 1.0, one per "column" of the overview waveform.

### How PWAV data is parsed

Each PWAV entry is a single byte. AutoCue extracts the amplitude from the lower 5 bits (`byte & 0x1F`), giving a raw value in the range 0–31. The upper 3 bits encode colour information used by Rekordbox's waveform display but are ignored by AutoCue.

The raw amplitudes are processed in three steps:

1. **Normalization**: divide each value by 31.0 to produce values in `[0.0, 1.0]`.
2. **Smoothing**: apply a 3-sample symmetric rolling average to reduce noise while preserving overall shape. End points are left unchanged.
3. **Downsampling**: chunk-average the smoothed curve to exactly `n_points` values (default 50 for general use, up to the full PWAV length for sparkline rendering).

The result is cached in memory keyed by `(track_id, n_points)` so subsequent calls within the same server session are instant.

### What the numbers mean

A value of `0.0` means silence or near-silence at that position. A value of `1.0` is the highest-amplitude section in the track. Each column represents approximately 150ms of audio.

The curve is also classified into one of four profiles:

- **flat** — variance below 0.05; consistent energy throughout. Common for minimal techno.
- **build** — second-half mean exceeds first-half mean by more than 0.05. Energy rises toward the end.
- **drop-then-flat** — energy peaks early then levels off. Classic EDM structure.
- **wave** — two or more distinct local maxima detected. Multiple energy peaks and valleys.

The profile label and a mini SVG sparkline appear on each track card in the web UI. The raw curve is served by `GET /api/tracks/{id}/energy`.

---

## Feature 3: Mixability Score

### What the score measures

The Mixability score (0–100) is a deterministic estimate of how easy a track is to mix in and out of. A high score means the track has a long intro you can blend over, a long outro to blend under, steady energy that does not surprise the next track's listeners, sparse or no vocals, and clear phrase structure with multiple mix points. A low score means the track drops immediately with no intro, has prominent vocals throughout, and offers few options for where to enter or exit.

The score requires phrase analysis data (PSSI). Tracks without it return `null` and show "No phrase data" in the UI.

### How it's calculated

Five components are combined with fixed weights:

| Component | Weight | Formula |
|---|---|---|
| Intro bars | 25% | `min(intro_bars / 32, 1.0)` — 32 bars scores 100%, 16 bars scores 50% |
| Outro bars | 25% | `min(outro_bars / 32, 1.0)` — uses track `Length` from DB; 0.5 neutral if length missing |
| Energy consistency | 20% | `1 − min(energy_variance × 5, 1.0)` — zero variance = 100%, variance ≥ 0.2 = 0% |
| Vocal proxy | 15% | `1.0` if no VERSE phrases detected, `0.3` if vocals present |
| Phrase structure | 15% | `min(phrase_count / 6, 1.0)` — 6+ phrases = 100% |

`intro_bars` is the total bars spanned by leading Intro phrases. The bar duration is derived from the PQTZ beat grid: the time span across all beat entries divided by the beat count gives the average milliseconds per beat; multiplied by 4 for 4/4 time.

`vocal_proxy` uses the presence of any VERSE-labelled phrase as a proxy for vocals. Rekordbox uses VERSE for sung sections, so this is a reliable signal even though AutoCue never analyses the audio itself.

The final formula:

```
score = (intro × 0.25 + outro × 0.25 + energy × 0.20 + vocals × 0.15 + structure × 0.15) × 100
```

### What a high vs low score means for a DJ

**Score 75–100:** Very mixable. Long intro and outro (24–32+ bars each), instrumental or sparse vocals, consistent energy level. You have plenty of time to blend, EQ, and set levels before the mix needs to be clean. Typical of well-produced techno, minimal, and deep house.

**Score 50–74:** Moderately mixable. May have a 16-bar intro, moderate energy variation, or occasional vocal passages. Still workable with experience. Typical of most house and techno.

**Score below 50:** Requires care. Short or no intro, prominent vocals, or highly variable energy. You may need to beatjump, use loops, or place the mix earlier/later than usual. Typical of vocal pop tracks or tracks with immediate drops.

### The animated breakdown panel

When you click the `Mix NN/100` chip on a track card, a breakdown panel expands below it. Each of the five components is shown as a labelled progress bar with a contextual extra detail: intro bars shows the actual bar count, outro bars shows its count, vocals shows "vocals detected" or "instrumental", and structure shows the phrase count. The score chip itself animates from 0 to the final value using a cubic ease-out over 600ms when first revealed.

The raw data is served by `GET /api/tracks/{id}/mixability`.

---

## Feature 4: Track Classification

### Five DJ set categories

AutoCue classifies every track into one of five categories that map to positions in a DJ set:

| Category | Label | BPM sweet spot | Energy | Vocals |
|---|---|---|---|---|
| `warmup` | Warm-up | 90–120 | Low | Neutral |
| `build` | Build | 118–128 | Medium | Slight penalty |
| `peak` | Peak | 126–145 | High (peak energy) | Penalty |
| `after_hours` | After-hours | 100–116 | Low–medium | Slight bonus |
| `closing` | Closing | 70–105 | Low | Neutral |

### How each category is scored

Each category uses a **trapezoidal membership function** for BPM and energy separately, then combines them. The trapezoid has four breakpoints: `lo_zero` (score = 0 below this), `lo_full` (score ramps up from 0 to 1), `hi_full` (score stays at 1), `hi_zero` (score ramps back down to 0 above this).

For example, the `build` category:
- BPM trap: `(108, 123, 123, 140)` — full membership at exactly 123 BPM; a 120 BPM track scores ~0.8 (rising ramp 108→123), falling off to 0 by 140
- Energy trap: `(0.1, 0.35, 0.55, 0.72)` — medium energy is ideal
- Combined: `bpm_score × (energy_score × 0.60 + 0.40)` — energy contributes 60%, but even with no energy data the BPM score still counts for 40%
- Vocal factor: `0.85` if vocals detected (builds benefit from being instrumental)

For `peak`, the energy component uses `energy_peak` (the maximum value in the curve) rather than `energy_mean`, because peak-hour tracks need to hit hard even if they have quieter sections.

When no ANLZ data is available and `energy_mean` is `None`, AutoCue substitutes `0.5` — a neutral default that caps the score at approximately 0.70. This prevents unanalysed tracks from all scoring 0 (which would make them invisible) while flagging that the score is uncertain.

### Multi-label scoring

A track receives a score for all five categories simultaneously. A 122 BPM track with medium energy may score 0.71 for `build` and 0.58 for `after_hours` at the same time. The highest score becomes the `primary` category shown in the UI badge. The full scores dict (`warmup`, `build`, `peak`, `after_hours`, `closing`) is available in the API response and used by Set Builder and Playlist Suggest.

### Confidence and primary category

The `confidence` field in the API response is simply the score of the primary category (0.0–1.0). If confidence is below 0.1, the UI removes the badge rather than showing a near-zero classification. A confidence of 0.0 on all categories means the track has BPM = 0 and no energy data — completely unanalysed.

Classifications are cached in memory by track ID for the duration of the server session. `GET /api/tracks/{id}/classification` returns the full result. `GET /api/classify` (SSE) streams one result per track for the whole library.

---

## Feature 5: Similar Track Discovery

### The 6-dimensional feature vector

For each track, AutoCue computes a feature vector from data already available in the database and analysis files:

```
[key_cos, key_sin, energy_mean, energy_variance × 10, vocal_proxy, bpm / 200]
```

**key_cos / key_sin**: The Camelot key (e.g. "8A") is converted to an angle on a 12-position circle (`2π × (number − 1) / 12`). Encoding the key as `(cos θ, sin θ)` instead of a raw integer makes the key space circular and continuous: "12A" and "1A" are adjacent, not far apart. The A/B ring distinction (major vs. minor) is collapsed — it is handled with more nuance in the transition scorer instead.

**energy_mean, energy_variance**: Mean and variance of the PWAV curve. Both are 0.0 when no ANLZ data is available.

**vocal_proxy**: 1.0 if any VERSE phrase was detected, 0.0 otherwise.

**bpm_norm**: BPM divided by 200, capped at 1.0. This normalizes BPM into the same 0–1 scale as the other dimensions.

The vector is L2-normalized before storage so that cosine similarity reduces to a dot product.

### How cosine similarity works

When you request similar tracks, AutoCue computes the dot product of the target track's normalized vector against every candidate's normalized vector. Because both vectors are unit-length, the dot product equals the cosine of the angle between them, which is 1.0 for identical vectors and decreases toward 0.0 as they diverge.

The full index (all tracks in your library) is held in process memory as a Python dict of `track_id → (bpm, vector, has_energy)`. Building the index takes a few seconds on first request (reading ANLZ files for every track), then all subsequent lookups are near-instant.

### BPM gate

Before computing similarity, AutoCue discards any candidate whose BPM differs from the target by more than `bpm_gate` (default ±8 BPM). This is a hard filter, not a penalty — tracks outside the gate are never scored. This prevents a 90 BPM track from being suggested as similar to a 135 BPM track even if their keys and energy profiles happen to align.

### Data-quality cap

If both the target track and a candidate lack ANLZ energy data (`has_energy = False`), their vectors are dominated by BPM and key alone. Two unanalysed tracks at the same BPM would otherwise score near 1.0. AutoCue caps the similarity at 0.65 in this case, and at 0.82 when only one track lacks energy data. This prevents your least-analysed tracks from all appearing as "100% similar" to each other.

### BPM distance penalty

A linear penalty of up to 15% is applied based on BPM difference: `score × (1 − min(bpm_diff / 20, 0.15))`. A candidate 4 BPM away scores 20% less than one at the same BPM. The 15% cap ensures the penalty never overwhelms the feature-based similarity, and matches the BPM weighting already present in the transition scorer.

The UI shows the top 5 similar tracks in a collapsible panel beneath each track card. Click "≈ Similar" to open it. Results show similarity percentage and BPM difference.

`GET /api/tracks/{id}/similar?n=10&bpm_gate=8.0` returns up to `n` results as `{track_id, score, bpm_diff}`.

---

## Feature 6: Transition Scoring

### What the score measures

`POST /api/transitions/score` takes two track IDs and returns a single **overall score (0–100)** measuring how cleanly track A can be mixed into track B. It decomposes into three independent components and provides human-readable explanations for each.

### BPM component (weight: 40%)

```
delta = abs(bpm_b − bpm_a) / bpm_a
score = 100 if delta ≤ 0.03
score = 0   if delta ≥ 0.10
score = 100 × (0.10 − delta) / 0.07  (linear interpolation between 3% and 10%)
```

Tracks within ±3% of each other score 100 (perfect — you can beatmatch without audible tempo drift). The score decays linearly to 0 at ±10%, where any beatmatch sounds clearly out of sync. If either BPM is 0, the score is 50 (neutral — no penalty for missing data).

**Half-time / double-time bonus**: if the ratio `bpm_b / bpm_a` is between 0.46–0.54 or 1.85–2.15, the score is fixed at 50. These ratios correspond to exact half-time and double-time relationships. The soft tolerance (±4% around the exact 2:1 ratio) prevents an abrupt cliff — a 0.479 ratio would score 0 without it.

### Key component (weight: 35%)

AutoCue parses Camelot notation (e.g. "8A") and computes circular distance on the 12-position wheel:

| Relationship | Score |
|---|---|
| Identical key (same number, same letter) | 100 |
| Adjacent on the wheel (±1 same letter, e.g. 8A → 7A) | 80 |
| Parallel (same number, different letter, e.g. 8A → 8B) | 75 |
| Cross-adjacent (±1, different letter, e.g. 8A → 7B) | 60 |
| Distance 2 | 50 |
| Distance 3 | 25 |
| Distance 4+ | 0 |

Unknown or missing keys return 50 (neutral). This scoring is intentionally strict: distance 4+ is 0 with no floor, reflecting that a genuine key clash is worse than no transition at all.

### Energy component (weight: 25%)

AutoCue reads the last 5 points of track A's energy curve (approximately 750ms before the end) and the first 5 points of track B's curve (approximately 750ms after the start), averaging each window.

```
delta = abs(end_energy_a − start_energy_b)
score = 100 if delta ≤ 0.05
score = 0   if delta ≥ 0.50
score = 100 × (0.50 − delta) / 0.45  (linear interpolation)
```

When energy data is unavailable, the score is a partial penalty rather than a perfect match — "unknown is not the same as a perfect handoff". If **both** tracks lack energy data the score is **50** (neutral); if only **one** side is missing, the computed score is **capped at 75** to reflect the uncertainty. So missing analysis lowers confidence in the energy component without fully tanking the transition.

### How to read the breakdown

The response includes an `explanation` array with one human-readable string per component:

```json
{
  "overall": 85.5,
  "bpm": 100.0,
  "key": 75.0,
  "energy": 71.1,
  "bpm_a": 124.0, "bpm_b": 124.5,
  "key_a": "8A",  "key_b": "8B",
  "end_energy_a": 0.62, "start_energy_b": 0.48,
  "explanation": [
    "0.5 BPM difference — perfect",
    "8A→8B — parallel (same number)",
    "Energy drops slightly (29%)"
  ]
}
```

An overall score ≥ 80 is an excellent transition. 60–79 is good. 40–59 is workable but requires skill. Below 40 is a clash.

---

## Feature 7: Set Builder

### What it produces

`POST /api/setbuilder` returns an ordered list of tracks forming a complete DJ set. Each track includes its title, artist, BPM, Camelot key, AutoCue category label, transition score from the previous track, and a **mixing tip** explaining how to execute each transition.

### Beam search algorithm explained

Rather than evaluating all possible track sequences (O(n²) per step, prohibitive for 10,000-track libraries), AutoCue uses beam search:

1. **Seed selection**: if no `seed_track_id` is provided, AutoCue scans all tracks and picks the one that best balances `bpm_score(start_bpm, track_bpm) × 0.5 + category_score(first_category) × 0.5`. A two-pass approach ensures the seed is at or above `start_bpm` — falling back to any BPM only if no in-range track is found.

2. **Per step**: for the current end track of each active beam, AutoCue calls `find_similar(track_id, n=40)` to get up to 40 similar candidates within the BPM gate. When building BPM, the gate looks asymmetrically further ahead (minimum ±12 BPM toward `end_bpm`) to ensure higher-tempo candidates are visible. This is O(n × 40) per step, not O(n²).

3. **Filtering**: each candidate must (a) not already appear in the beam by track ID (deduplication), (b) not share the same title + artist as an existing track (blocks duplicate imports), (c) not exceed the per-artist repeat cap (default: max 2 appearances per artist), (d) have BPM within `[current_bpm × 0.97, current_bpm × (1 + bpm_step_max)]` — and when building BPM, never descend below `start_bpm × 0.97`, (e) score ≥ 0.3 for the target category at this point in the set, and (f) score ≥ 40 overall on the transition scorer.

4. **Scoring**: when `start_bpm ≠ end_bpm`, the scoring uses a setbuilder-specific weighting (`0.25 × BPM + 0.40 × key + 0.35 × energy`) instead of the standard transition scorer weighting. This prevents the standard BPM component from penalising intentional tempo movement. A **BPM-progress bonus** (up to +15 points) rewards tracks that move toward `end_bpm`, proportional to the fraction of remaining BPM distance covered. The adjusted score is `reweighted_overall − energy_penalty + bpm_bonus`.

5. **Beam maintenance**: at the end of each step, the top 5 new beams (by cumulative adjusted score) are kept. This explores 5 parallel paths simultaneously so a locally suboptimal track can still lead to a better overall set.

6. **Termination**: the algorithm stops when all beams have accumulated enough track duration to meet `duration_minutes`, or when the safety cap (`estimated_tracks × 3` iterations) is reached.

### Start/end BPM, duration, energy mode

**start_bpm** and **end_bpm** define the tempo arc. If `end_bpm ≥ start_bpm`, the category sequence is `warmup → build → peak` (ascending energy). If `end_bpm < start_bpm`, it is `peak → after_hours → closing` (descending).

**duration_minutes** is the target total length. The algorithm estimates approximately 6 minutes per track to set how many steps to run.

**energy_mode** (`build`, `drop`, `flat`) applies a soft penalty of 15 points when a candidate's start energy contradicts the intended direction:
- `build`: penalise candidates whose start energy is more than 0.15 below the previous track's end energy (you want energy to increase or hold)
- `drop`: penalise candidates whose start energy is more than 0.15 above (you want energy to decrease)
- `flat`: penalise candidates that jump or drop by more than 0.15 in either direction

**bpm_step_max** (default 8%) limits how fast the BPM can increase per track. A maximum 8% step means a 120 BPM track can be followed by at most a 130 BPM track. You can tighten this to 4% for very smooth BPM transitions.

### Category sequencing

At each step, the target category is computed by mapping the step index into the category sequence. For a 10-track set with sequence `[warmup, build, peak]`:
- Steps 0–3: target `warmup`
- Steps 4–6: target `build`
- Steps 7–9: target `peak`

A track must score ≥ 0.3 for the target category to be a candidate. Because the category trapezoids are BPM-gated (e.g. `build` starts at 108 BPM), the category arc only fires naturally once the BPM has climbed to the correct range. The BPM-progress bonus ensures the beam climbs BPM before worrying about the category filter.

### Seed track option

If you provide `seed_track_id`, the set always starts with that specific track. This is useful when you know how you want to open your set and want AutoCue to complete the rest.

### Duplicate and repeat prevention

Each beam maintains:
- A `visited` set of track IDs — no track appears twice in the same beam
- A `visited_titles` set of `"title|||artist"` strings — blocks the same song from appearing under two different track IDs (common with re-imports)
- A `visited_artists` counter — caps any single artist at 2 appearances per set (configurable)

### DJ mixing tips (`mix_advice`)

Each track in the result (except the first) includes a `mix_advice` string describing how to execute the transition. The tip is derived from the transition scorer components:

| Situation | Example tip |
|---|---|
| Matched BPM, same key | `BPM matched — beatmix, blend over 16–32 bars` |
| +5 BPM, adjacent key | `Nudge pitch +5.1 BPM — blend over 8–16 bars; compatible key (5A→4A) — harmonic blend works` |
| Large BPM gap | `18.4 BPM gap — hard cut at phrase boundary or use an acappella/dub` |
| Key clash | `BPM matched — beatmix, blend over 16–32 bars; key clash (1A→7B) — EQ-kill lows/mids before incoming lands` |
| Energy jump | `BPM matched — beatmix, blend over 16–32 bars; energy jumps 25% — filter incoming until mix point, then open slowly` |
| Half-time drop | `Half-time drop (120→60 BPM) — let outgoing finish, bring incoming in at full energy` |

In the web UI, tips appear between rows in the connector line, styled as italic hints (💡).

---

## Feature 8: Playlist Suggest

### What it does

`POST /api/playlists/suggest` returns a ranked list of tracks from your library (or a specific playlist) that best match a given DJ category. You specify the category (`warmup`, `build`, `peak`, `after_hours`, `closing`), a count (1–500), and optionally a `playlist_id` to narrow the source and a list of `exclude_ids` to skip tracks you have already used.

### Category scoring and ranking

Every track in scope is passed through `get_classification()`, which returns a scores dict with one value per category. The value for the requested category is extracted and used as the sort key. Tracks scoring 0 are omitted entirely. The top `count` tracks are returned as `{track_id, score}` pairs.

### How analyzed vs unanalyzed tracks are ranked differently

Analysed tracks (those with PWAV energy data) receive full scores from the BPM + energy trapezoid formula. Unanalysed tracks receive only the BPM component with a neutral energy default, capping their score at approximately 0.70 × their BPM score. This means a perfectly-BPM-matched but unanalysed track will rank below a well-matched analysed track.

### Excluding already-played tracks

Pass a list of track IDs in `exclude_ids` to filter out tracks you have already played in your set. The endpoint checks each `content.ID` against the exclude set before scoring. This integrates cleanly with Set Builder: after building a set, you can pass its track IDs as `exclude_ids` when calling Playlist Suggest to fill out the rest of your crate without repeats.

---

## Feature 9: Library Health Check

### How it works

`GET /api/health` (SSE) or `GET /api/tracks/{id}/health` scan your library track by track and emit a JSON health report for each one. The scan is pure database reads — no ANLZ parsing, no audio analysis — so it can scan thousands of tracks per second.

### Issue codes and what they mean

| Code | Severity | Score impact | Description |
|---|---|---|---|
| `NO_AUDIO_FILE` | error | Forces score = 0 | The audio file path stored in `DjmdContent.FolderPath` does not exist on disk. The track is dead weight in your library. |
| `NO_CUES` | error | −30 | No hot cues (DjmdCue rows with Kind 1–8) exist for this track. AutoCue can fix this. |
| `NO_PHRASE` | info | −10 | `AnalysisDataPath` is empty — Rekordbox never ran phrase analysis. Re-analyze in Rekordbox. |
| `NO_BEATGRID` | info | −10 | `BPM` in `DjmdContent` is 0 or missing — no beat grid. Re-analyze in Rekordbox. |
| `DUPLICATE_CUE` | warning | −5 | Two or more hot cues have `InFrame` values within 2 frames of each other (< 13ms). This usually indicates a double-write bug. Only one duplicate penalty is applied regardless of how many duplicates exist. |
| `UNNAMED_CUES` | info | −5 | At least one hot cue has an empty `Comment` or a name matching the pattern `Cue N` (Rekordbox's default). |
| `NO_MEMORY_CUE` | info | 0 | No memory cue (Kind=0) exists. The CDJ won't auto-position to a cue point on load. No score penalty, but worth knowing. |

### Score calculation

All tracks start at 100. Deductions are applied for each issue detected. `NO_AUDIO_FILE` is a special case that immediately returns score 0 and skips all other checks. The final score is clamped to `[0, 100]`.

A track scores 100 only if it has: an existing audio file, at least one named hot cue, phrase analysis data, and a beat grid. The `NO_MEMORY_CUE` issue is informational and does not affect the score.

### Fix tiers

Alongside the score, each report includes a `fix_tier` that describes the best quality of automated fix available:

| Tier | Condition | Auto-fix confidence |
|---|---|---|
| `phrase` | Phrase data + beat grid both present | 1.0 — phrase-accurate cues possible |
| `bar` | Beat grid present, no phrase data | 0.6 — bar-interval cues possible |
| `heuristic` | No beat grid | 0.3 — 30-second estimate only |
| `none` | Audio file missing | Cannot generate cues |

### Running on a playlist vs entire library

Pass `?playlist_id=N` to limit the scan to tracks in a specific playlist. This is useful for incremental rescans: after re-analyzing a batch of tracks in Rekordbox, filter to that playlist in the UI and run the health check again to see which tracks are now fixable at phrase quality.

The SSE stream emits one JSON event per track (as a `TrackHealthReport` schema), then a final `{"done": true, "summary": {...}}` event with aggregate counts.

---

## Feature 9b: Duplicate Tracks

The **Duplicate Tracks** panel (the **Duplicates** rail place in the workbench) finds tracks that exist more than once in your library, suggests which copy to keep, and lets you delete the rest with a backup-before-write safety net and one-click undo.

### Finding duplicates

Click **Find duplicates**. AutoCue groups tracks by **artist + title + duration** (case-insensitive, 5-second duration buckets). The duration bucket is what keeps a *4:12 album cut* and a *6:48 extended mix* of the "same" song from being wrongly merged, while still grouping two imports whose tagged lengths differ by a second or two. The summary shows how many groups, how many surplus copies, and how many empty-metadata streaming tracks were skipped.

### The keeper

Each group highlights one copy as the **★ keeper** — the one AutoCue suggests you keep. The pick order is:

1. **Most hot cues** — your cue-prep work wins. A freshly-prepped re-import beats a heavily-played copy with only auto-cues.
2. **Most plays**
3. **Most recently played**
4. **Highest bitrate** — a 320 kbps re-rip beats a 192 kbps original.
5. Lowest track ID (deterministic tiebreak).

Expand a group to see every copy with its cues / plays / bitrate / last-played, and a **"Keep" radio** on each — pick a different keeper and the delete button + chips update instantly.

### Same-file vs distinct-file

Each non-keeper shows a chip:

- 🗂 **same file as keeper** — this row points at the same audio file; deleting it leaves no orphan on disk.
- 📁 **distinct file — stays on disk** — a separate audio file that will remain on disk after the library row is removed.

### Deleting

Delete a single group's non-keepers, or use **Delete all N non-keepers** for the whole library. Either way:

- A **confirm dialog** opens (its Delete button is disabled for a moment so a stray Enter can't fire it). It tells you how many distinct audio files will remain on disk.
- **Rekordbox must be closed** (the panel refuses with a clear message otherwise).
- A **backup of your library** is created before the first delete. Repeated deletes within 30 seconds share one backup, so restoring it undoes the whole cleanup session.
- A **progress bar** fills as tracks are deleted; you can **Cancel** mid-way (already-deleted tracks stay deleted, the backup still restores everything).
- On success an **"Undo this delete"** button appears for 30 seconds — one click restores the backup.

### What is not deleted

Deleting a duplicate removes it from your Rekordbox library and all its cues / playlists / tags / history — but it does **not** delete the audio file from disk. The 📁 chip tells you which deletes leave an orphan file behind so you can clean those up manually if you want.

---

## Feature 10: Cue Library Tools

### Operations

`POST /api/cue-tools-stream` applies bulk edits to hot cues across all visible/filtered tracks. Four operations are available:

**rename** — replace an exact cue name across all tracks. For example, rename all cues called "Cue 1" to "Drop". The match is exact (case-sensitive, no wildcard). Cues that do not match the `from_name` are skipped, not deleted.

**recolor** — set the `ColorTableIndex` for specific hot cue slots. You provide a map of `{slot_index: color_index}` (e.g. `{"0": 1, "1": 2}` to color slot A red and slot B orange). Slots not in the map are untouched. Useful for normalising color conventions across your library.

**shift** — move all hot cues on all affected tracks forward or backward by a fixed number of milliseconds (`delta_ms`). Both `InMsec` (position) and `InFrame` (`round(InMsec × 150 / 1000)`) are updated. If a cue has a loop out-point (`OutMsec ≥ 0`), it is shifted by the same delta to preserve loop length. Cues that would shift to a negative position are skipped. This is useful for correcting a systematic beatgrid offset across a batch of tracks.

**delete_orphan** — delete all hot cues in slots above a specified number. For example, `keep_slots = 4` deletes cues in slots E, F, G, H (Kind 5–8) while keeping A–D. Useful for trimming cue sets that AutoCue filled to 8 slots when you only want 4.

### Dry-run mode

Dry run is enabled by default in the UI. In dry-run mode, the server computes how many cues would be affected and streams progress events, but no database writes occur. You see the "X cues renamed across Y tracks (dry run)" result before committing. Disable dry run and click again to execute.

### Auto-backup before write

Before any write (when `dry_run = false` and there are tracks to process), AutoCue copies `master.db` to `~/.autocue/backups/master_TIMESTAMP.db`. This backup is created before the first write, so if the operation fails partway through you can always restore.

### Running on visible/filtered tracks

The web UI always sends the IDs of the currently visible tracks (after applying any search query, playlist filter, phrase-only filter, or beat-grid-only filter) to the API. There is no "apply to all" mode — you always see exactly which tracks will be affected before committing.

The **Beat grid only** (`♪`) toggle next to **Phrase only** narrows the list to tracks Rekordbox has actually analyzed (BPM > 0). Combine the two to surface fully-analyzed tracks; turn either off to find tracks that still need a pass through Rekordbox's analyzer. The two flags share the same client-side `filteredTracks()` plumbing as every other filter — selection, export, and bulk write endpoints all see the narrowed list.

The third toggle **Audio available** (`🔌`) hides tracks whose audio is unreachable — streaming-source rows imported from Spotify or Tidal, plus files whose path no longer exists on disk. The check is lazy and fail-open: when the music folder is on an unmounted external drive or unreachable network share, the unverifiable tracks **stay visible** so you don't accidentally hide your library. Tracks confirmed missing get a muted "No audio ⓘ" chip on their card instead of the Load audio button. Clicking the chip opens a track info modal that shows the stored file path plus a **Download via YouTube** button — the candidate-selection flow lets you preview titles, channels, and duration before committing, so a missing track can be rescued with a real audio file in seconds. After a successful download, the modal exposes a Copy path button and a short Rekordbox 7 relink instruction; AutoCue intentionally does not auto-rewrite the Rekordbox file path (that DB mutation is yours to make, deliberately).

### Phrase-analysis progress banner

Switching into ✨ Phrase analysis with a multi-thousand-track library used to freeze Chrome long enough to trigger the "Page Unresponsive" dialog. The Cues tab now shows a persistent progress banner (pinned below the *Tracks* title, above the filter row) with a live "Computing phrase cues N / M" counter, a thin progress bar, and a Cancel button. Cards update individually as their phrase cues arrive — no library-wide flicker. Cancelling stops the load cleanly; the next mode toggle within the same library state reuses cached phrase data and updates only the visible cards (~5 ms client-only operation).

---

## Feature 11: BPM Color Coding

### What it does

`POST /api/color-tracks-stream` sets the `DjmdContent.ColorID` field for each track based on its BPM range. The color appears as a colored dot beside the track name in Rekordbox's browser. It is a visual BPM coding system for quick set preparation.

### BPM ranges and colors

| BPM range | Rekordbox color | SortKey |
|---|---|---|
| < 90 | Aqua | 6 |
| 90–114 | Green | 5 |
| 115–124 | Blue | 7 |
| 125–134 | Orange | 3 |
| 135–149 | Red | 2 |
| ≥ 150 | Pink | 1 |

The color IDs are resolved at runtime by querying the `DjmdColor` table — color IDs in Rekordbox 7 are UUID strings, not integers, so AutoCue maps `SortKey → ID` dynamically rather than hardcoding values.

### skip_colored option

If the "Skip already colored" checkbox is enabled in the UI, tracks that already have a `ColorID` set are left unchanged. This lets you color just the newly added tracks in your library without overwriting any manual color assignments.

---

## Feature 12: Backup and Restore

### How backups work

Every write operation that modifies `master.db` automatically creates a timestamped backup before the first write: `~/.autocue/backups/master_YYYYMMDDTHHMMSS.db`. AutoCue copies the database file using `shutil.copy2`, which preserves file metadata.

If WAL mode is active (Rekordbox uses WAL by default), the `-wal` and `-shm` sidecar files are also copied to ensure the backup is self-consistent and can be opened independently.

The backup is created regardless of whether the operation succeeds. If the backup itself fails, the operation is aborted with a 500 error — AutoCue never writes to the live database without a backup in place.

### Multi-select delete

The Backups panel lists all `.db` files in `~/.autocue/backups/` sorted by modification time (newest first). Each entry shows the timestamp, file size in MB, and a checkbox. Select one or more backups and click "Delete selected" to remove them. AutoCue issues one `DELETE /api/backups/{filename}` request per selected file and shows a consolidated toast. Sidecar files (`-wal`, `-shm`) are also deleted if present.

Path traversal is blocked server-side: only bare filenames (no `/`, `\`, or `..`) are accepted, and the resolved path must remain within `BACKUP_DIR`.

### Restoring

Click "Restore" on any backup entry to replace the live `master.db` with that backup. The server:

1. Checks that Rekordbox is not running (aborts with 409 if it is)
2. Calls `db.session.close()` and `db._engine.dispose()` to flush WAL and release all file locks
3. Copies the backup file over `master.db` and handles WAL/SHM sidecars
4. Re-opens the database via `Rekordbox6Database` and updates `app.state.db`
5. Clears all in-memory analysis caches (energy, classification, mixability, similarity index) so the restored library's data is re-read fresh

`POST /api/restore` accepts `{filename}` (the base filename, not a full path) and returns `{restored: true, message: "Restored from <filename>"}`.

---

## Feature 13: Cue Reasoning Panel

The Cue Reasoning panel explains exactly why AutoCue placed each cue where it did. Click the `ℹ` icon on any cue badge in the web UI to open it (click again to close). It is designed for touch screens — hover is not used.

### Phrase mode explanation

When a cue was generated from phrase data, the panel shows:

- The Rekordbox phrase type: "Rekordbox phrase: Chorus (high-energy section)", "Rekordbox phrase: Up (energy rise)", etc.
- The phrase length in bars: "8-bar phrase"
- The slot priority reason: "Slot A: mix-in point (first non-Intro phrase)", "Priority slot: main drop", "Priority slot: energy build", "Priority slot: outro/mix-out"

These reasons are derived from the cue's `label`, `phraseMode`, `phraseBars`, and `slot` fields returned by `/api/generate`.

### Bar mode — with vs without phrase data

If the track has phrase data available but you chose bar-interval mode, the panel notes: "Using bar intervals — switch to Phrase mode to use Rekordbox phrase data". If the track genuinely has no phrase analysis, it says: "Bar-interval fallback (no Rekordbox phrase analysis)" and advises re-analyzing in Rekordbox.

In both cases the actual bar name (e.g. "Bar 33") is shown as the position.

### Heuristic mode

"No BPM or phrase data — 30-second interval estimate" plus the timestamp name (e.g. "1:30"). Low confidence, but at least the user understands why the cue is where it is.

### Memory cue

Memory cues (slot = −1) always show "CDJ load point (Auto Cue)" and "Anchored to earliest phrase boundary". The confidence label is "Auto" rather than High/Medium/Low because memory cues do not have the same placement certainty concept.

### Manual cue

If a cue has `confidence == null && phraseMode == null` — indicating it was placed in Rekordbox by the user, not generated by AutoCue — the panel shows "Manually placed cue" with a confidence indicator of "—". AutoCue never claims ownership of cues it did not create.

### Confidence levels

- **High** (confidence ≥ 0.9): phrase mode, beat-accurate
- **Medium** (0.5 ≤ confidence < 0.9): bar mode, accurate to within a bar
- **Low** (confidence < 0.5): heuristic, rough estimate
- **Auto**: memory cue
- **—**: manually placed, AutoCue has no information

---

## Feature 14: New-Release Discovery (Discover v2)

### What it does

The **Discover** tab (local server mode only) is a personalised feed of new releases driven by your own listening history. It blends three independent sources — artists you play, labels you follow, and adjacent finds you don't yet know — so the feed surfaces both retrieval (your familiar territory) and exploration (one step beyond) every scan.

### How it works

1. **Taste vector** — On scan start, AutoCue walks your library to count plays per artist, label, and style. This becomes your taste profile; it's recomputed each scan so the feed tracks your library as it grows.
2. **Three feeders run in sequence**, each with a hard request budget:
   - **Artist watch** (20 requests) — your top 20 most-played artists, page-1 of recent releases each.
   - **Label watch** (15 requests) — the labels you've explicitly followed, longest-unscanned first, with a 24-hour TTL so a freshly-scanned label isn't re-hit until tomorrow.
   - **Novelty** (10 requests) — one of three rotated strategies per scan: style-adjacent (releases in styles next to your top styles in a curated graph), label-adjacent (parent + sub-labels of labels you follow), or artist-adjacent (groups + member projects of artists you play). The rotation cycles so over three scans you cover all three angles.
3. **Hard cap**: total ≤ 60 requests per scan, matching Discogs' authenticated rate-limit window. The orchestrator validates this at scan start and refuses any feeder budget table that sums above 60.
4. **Ranker** scores every candidate (taste match + novelty bonus + freshness + already-owned guard) and produces a deduped feed. Results stream in live via SSE; the UI renders cards as soon as the first feeder yields its first hit.
5. **Persistent state** — saves / dismisses / snoozes / followed labels / blocked artists+labels all live in a sidecar `discover.db` next to your master.db. The state survives reloads and rides along with your backups.

### Controls

- **Source filter chips** — toggle artist / label / novelty on or off to focus the scan.
- **Year filter** — All / This year / Last 2 years.
- **Sort** — Taste match (the ranker's default) / Newest / Title / Artist / Explore mode (50/50 round-robin of novelty against the rest).
- **Refresh** — re-run the scan; the prior result clears immediately.
- **Cancel** — cancel a running scan; the orchestrator backs out cleanly.

### Per-card actions

Every card carries hover-reveal **Save / Snooze / Dismiss** buttons; clicking the card opens a detail panel with the full tracklist, a Discogs link, a YouTube preview carousel (up to 3 candidates, lazy-loaded), a **Download album** action, contextual **Follow this label** and **Block artist / label** CTAs, and a save-to-favorites toggle.

### Snooze popover

Clicking 💤 opens a popover anchored at the button with three durations: **1 week**, **1 month** (default), **3 months**. Cards whose snooze has since expired and surfaced in a later scan get a **🔁 Resurfaced** badge so you know it's coming back from the snooze pile.

### Power-user flow

- **Shift+click a card** — bypass the detail panel and jump straight to a confirmation modal whose default focus is **Cancel** (so sticky-Shift + accidental-Enter never starts a download).
- **Keyboard shortcuts** — `j` / `k` navigate cards, `Enter` opens the detail panel, `s` saves, `x` dismisses, `z` opens the snooze popover, `D` (Shift+d, on purpose) opens the download confirm, `?` toggles a help overlay.

### Settings sub-panel

Under the **⚙ Settings** button next to the Discover header:
- **Labels you watch** — list of followed labels with last-scanned time, an Unfollow button per row, an inline **Search Discogs to add a label** input, and a **Suggest** button that lists labels surfaced from your library's existing Discogs metadata.
- **Blocked artists / labels** — separate "Artists (N)" and "Labels (M)" sections with Unblock buttons per row.
- **Sync between machines** — Export discover.db as a timestamped `.gz` (so re-exports don't overwrite); Import on another machine to share saves / dismisses / follows / blocks. Import surfaces a per-field diff in the success toast.
- **Stats** — total scans, avg scan duration, saves per scan, novelty mix per strategy, top label / artist sources.

### Requirements

A Discogs personal access token (free, from discogs.com/settings/developers). Set it via the `DISCOGS_TOKEN` environment variable or your project `.env`. The Discover place's onboarding banner walks you through following labels the first time you open it.

---

## Feature 15: YouTube Download

### What it does

The **Download** panel (in the Discover place) fetches audio from YouTube using [yt-dlp](https://github.com/yt-dlp/yt-dlp) and extracts it to an audio file. You can download a track directly from any New-Release suggestion's **Download** button, or paste a YouTube URL / search term into the manual box.

### Setup

Download support is an **optional** add-on so the core app stays lightweight:

```bash
pip install -e ".[download]"   # installs yt-dlp
```

You also need an **ffmpeg** binary on your PATH (yt-dlp uses it to extract audio). On macOS: `brew install ffmpeg`. The panel detects whether both are present (`GET /api/download/config`) and shows install instructions if anything is missing.

### Where files go

Downloads are saved to `~/Music/AutoCue` by default. Override this with the `AUTOCUE_DOWNLOAD_DIR` environment variable before starting the server. The active destination is shown in the Download panel.

### How it works

- A real `http(s)` URL is downloaded directly; a bare search term is resolved to the best YouTube match (`ytsearch1:`).
- Audio is extracted to MP3 (192 kbps) via ffmpeg.
- Each request is **enqueued** (`POST /api/download/enqueue`, or `POST /api/download/album/enqueue` for a multi-track album) and returns a `job_id`; progress then streams over Server-Sent Events from `GET /api/download/stream/{job_id}`, so the UI shows a live percentage and the final saved path. The queue is inspectable and cancelable (`GET /api/download/queue`, `POST /api/download/cancel/{job_id}`). _(The older one-shot `POST /api/download` / `/api/download/album` endpoints are deprecated — use the enqueue→stream flow.)_

### Legal note

Downloading copyrighted audio from YouTube may violate YouTube's Terms of Service and copyright law. Only download content you are authorised to download — your own uploads, Creative-Commons material, or tracks you are otherwise licensed to use. Lawful use is your responsibility; AutoCue surfaces this disclaimer in the Download panel.

---

## Feature 16: Performance & Caching

AutoCue is built to stay responsive on libraries of 10,000+ tracks. Several systems work together to keep first-load fast, repeat-load near-instant, and multi-track endpoints (generate-apply, health, classify, auto-tag, comment enrichment, similarity index) parallel where it's safe to do so. Most of this is automatic — the controls below let you tune, inspect, or reset it.

### Sidecar analysis cache

The first time `autocue serve` runs against a library it populates a sidecar SQLite file at `<rekordbox_dir>/autocue_cache.sqlite`, alongside `master.db`. The cache memoizes work that's expensive to recompute every session: energy curves, classifications, similarity vectors, mixability scores, and the `/api/tracks` snapshot that powers the library list. On the next launch, anything still valid is loaded from the cache instead of re-derived from ANLZ files, taking cold-start similarity-index build from ~30 s down to a couple of seconds.

The cache is plain SQLite. It contains **no audio, no credentials, and no Discogs tokens** — only numeric features, labels, and basic track metadata (titles, artists, BPM, key, file paths). Per-track entries are invalidated automatically when the underlying ANLZ file's modification time changes; the whole cache is invalidated when `master.db` itself changes. You don't need to manage it.

### Resetting the cache

If you ever want to rebuild from scratch — after a Rekordbox library overhaul, when debugging, or when migrating between machines — start the server with the `--reset-cache` flag:

```bash
autocue serve --reset-cache
```

This deletes `autocue_cache.sqlite` before booting. The server starts immediately and re-warms in the background; the first scan or library list may take a few seconds longer than usual while the cache rebuilds.

### Warm-up progress badge

After `autocue serve` boots, AutoCue runs a background warm-up pipeline that populates the `/api/tracks` snapshot and the sidecar cache. The UI is usable during this time — you can browse, filter, and generate cues — but classification, similarity, and other intelligence features become snappier as the warm-up progresses. The status bar at the top of the app (`#app-status`) shows a live **"Indexing N / M tracks"** badge while warm-up runs, and disappears when it finishes. On a 10k library this typically completes in well under a minute.

### Fast library load (`/api/tracks`)

The library list is served from an in-memory snapshot keyed by `master.db`'s modification time. On a 10k library a warm load comes back in under 200 ms. Two behaviours make this efficient in practice:

- **ETag revalidation** — `/api/tracks` returns an `ETag` header. The web UI sends it back as `If-None-Match` on subsequent loads, and the server returns `304 Not Modified` (no body) when nothing has changed. Tab switches and refreshes cost almost nothing.
- **Optional NDJSON streaming** — Clients that send `Accept: application/x-ndjson` receive the response as newline-delimited JSON, one track per line, so they can start rendering before the whole library has arrived. The default JSON-array response is unchanged.

You don't need to opt in to either; the web UI uses them automatically.

### Thread-pool size (`AUTOCUE_POOL_SIZE`)

Multi-track endpoints share a single bounded thread pool. The default size is `min(8, cpu_count())`, which is the sweet spot for the I/O-bound ANLZ reads that dominate runtime. If you want to override it — for example to limit CPU on a laptop running on battery, or to push higher on a fast desktop — set `AUTOCUE_POOL_SIZE` before starting the server:

```bash
AUTOCUE_POOL_SIZE=4 autocue serve
```

Larger values give diminishing returns once disk I/O saturates. The single-writer rule for `master.db` is always preserved: parallel work happens in compute stages, but writes go through one thread.

### Optional parallel paths

Six analysis paths have a flagged parallel implementation alongside the proven serial path. Each is gated by an environment variable so you can opt in selectively:

| Env var | What it parallelises |
|---|---|
| `AUTOCUE_PARALLEL_GENERATE_APPLY` | `/api/generate-apply-stream` (cue generation + write) |
| `AUTOCUE_PARALLEL_HEALTH` | `/api/health` library scan |
| `AUTOCUE_PARALLEL_CLASSIFY` | `/api/classify` SSE stream |
| `AUTOCUE_PARALLEL_AUTO_TAG` | `/api/auto-tag` |
| `AUTOCUE_PARALLEL_ENRICH_COMMENTS` | `/api/enrich-comments` |
| `AUTOCUE_PARALLEL_SIMILAR` | Similarity index build |

Set any flag to `1` to enable the parallel path for that endpoint:

```bash
AUTOCUE_PARALLEL_HEALTH=1 AUTOCUE_PARALLEL_CLASSIFY=1 autocue serve
```

These are off by default to keep the shipping behaviour conservative. If you're scanning thousands of tracks regularly, enabling them yields large wall-clock wins (Library Health on 10k drops from ~16 min to ~2 min, for example).

### Performance instrumentation (developer)

For diagnosing slow operations there are two opt-in helpers:

- **Server-side ring buffer** — Set `AUTOCUE_PERF=1` before starting the server to enable the perf ring buffer and a `GET /api/perf/recent` endpoint. It returns the last 100 instrumented spans with handler name and p50/p95/p99 latency. Off by default; intended for development and bug reports.
- **Client-side console marks** — In the browser DevTools console, run `localStorage.autocue_perf = '1'` and reload. The web app then logs `[AutoCue Perf] …` measurements for key UI operations (initial load, filter, scroll, tab switch). Set the value to `'0'` (or clear it) to disable.

Neither is needed for normal use — they exist so you can attach concrete numbers to a perf issue when reporting one.

---

## Feature 17: Library Enrichment — My Tags, Comments & Discogs

Beyond cues, AutoCue can write its analysis back into Rekordbox's own metadata fields so it shows up natively in the browser and on CDJs. All three writers are local-mode only, run behind the "Rekordbox closed" guard, take a backup first, and are fully reversible.

### Auto-Tag (My Tags)

`POST /api/auto-tag` writes AutoCue's analysis as native Rekordbox **My Tags** (`DjmdMyTag` / `DjmdSongMyTag` rows), so you can filter and build smart playlists by them in Rekordbox itself. Tags are organised into eight groups:

| Group | Example tags |
|---|---|
| **category** | Warmup, Build, Peak, After Hours, Closing |
| **vocal** | Vocal, Instrumental |
| **energy_level** | High / Mid / Low Energy |
| **energy_profile** | Build / Wave / Flat / Drop Track |
| **intro_outro** | Long / Short Intro, Long / Short Outro |
| **decade** | 60s … 20s |
| **bpm_tier** | <120, 120–124, 125–128, 129–135, 136–144, >144 BPM |
| **play_history** | Never / Rarely / Frequently Played |

Each tag group is created once (idempotent) with a Rekordbox colour attribute. `dry_run` previews the changes; an overwrite option replaces AutoCue's previous tags; and `POST /api/auto-tag/undo` removes exactly what a run added (it tracks every inserted `DjmdSongMyTag`).

### Comment enrichment

`POST /api/enrich-comments` (and `/stream` for live progress) writes DJ-useful metadata to each track's **Comment** field (`DjmdContent.Comment`), which CDJs display under the title. For an empty comment the format is:

```
8A - Energy 7 | Peak | 4 bar intro
```

When a comment already exists, AutoCue appends inside a sentinel block instead of clobbering it — `existing text /* AutoCue: 8A / Peak / 4 bar intro */` — matching the convention Rekordbox uses for "Add My Tag to Comments", so the block is identifiable and removable. Output is capped at ~256 characters (the CDJ-readable limit). `/preview` shows the result without writing; `/undo` strips exactly the AutoCue block, restoring the original comment.

### Discogs genre & style tags

`POST /api/auto-tag/discogs` looks each track up on Discogs and adds its genres/styles as My Tags, giving you accurate genre metadata Rekordbox's own analysis doesn't provide. It requires a free Discogs personal access token (`DISCOGS_TOKEN` env var or `.env`); `POST /api/auto-tag/discogs/test` validates the token. Requests are rate-limited to stay within Discogs' API budget, and (like the others) support dry-run + undo.
