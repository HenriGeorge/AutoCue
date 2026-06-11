# AutoCue

Automatically place hot cues on every track in your Rekordbox 7 library — and get deep
intelligence about your tracks, transitions, and sets.

**[Try the web app →](https://henrigeorge.github.io/AutoCue/)**
&nbsp;·&nbsp;
[Local server](#local-server-autocue-serve)
&nbsp;·&nbsp;
[Python CLI](#python-cli)

---

## Three ways to use

| | Web app | Local server | CLI |
|---|---|---|---|
| Install required | No | Yes (Python) | Yes (Python) |
| Export XML first | Yes | No | No |
| Import XML after | Yes | No | No |
| Phrase analysis | Yes (drop ANLZ folder) | Yes (automatic) | Yes (automatic) |
| Cue placement strategies | Bar intervals, Phrase | Auto (phrase → bar → heuristic) | Auto (phrase → bar → heuristic) |
| Writes directly to Rekordbox | No | Yes | No (XML output) |
| Library health check | No | Yes | No |
| Mixability & energy scores | No | Yes | No |
| Similar track discovery | No | Yes | No |
| Transition scoring | No | Yes | No |
| Set builder | No | Yes | No |
| Cue library tools | No | Yes | No |

---

## Web app (no install)

1. In Rekordbox: **File → Export Collection in rekordbox format** → save as `rekordbox.xml`
2. Open [henrigeorge.github.io/AutoCue](https://henrigeorge.github.io/AutoCue/)
3. Drop your `rekordbox.xml`
4. Adjust bar-interval settings or switch to **✨ Phrase analysis** mode
5. Download `autocue_import.xml`
6. In Rekordbox: **File → Import Library** → select the file

Everything runs in your browser — no files are uploaded anywhere.

#### Analysis modes

| Mode | How cues are placed | Requires |
|---|---|---|
| **Bar intervals** | Every N bars from bar 1, using BPM + beat grid from the XML | Just the XML |
| **✨ Phrase analysis** | At detected phrase boundaries (Intro, Verse, Chorus, Outro) | XML + Rekordbox `share/` analysis folder |

#### Cue colours (phrase mode)

| Phrase type | Cue name | Colour |
|---|---|---|
| Intro | Intro | Green |
| Verse | Verse | Blue |
| Chorus (drop) | Drop | Red |
| Bridge | Bridge | Cyan |
| Outro | Outro | Orange |
| Up (build-up) | Build | Pink |
| Down (breakdown) | Break | Purple |

#### Backup & safety

- **Download backup XML** saves your original file before any changes.
- The output XML only replaces cue slots AutoCue uses — manually placed cues in other slots
  survive the import unchanged.
- A conflict warning highlights tracks where existing hot cues will be overwritten.

---

## Local server (`autocue serve`)

The fastest end-to-end workflow. Run a local server that reads your Rekordbox database
directly — no XML export, no XML import.

#### Install

```bash
git clone https://github.com/HenriGeorge/AutoCue.git
cd AutoCue
pip install -e .
```

#### Run

```bash
# Start the server — opens http://localhost:7432 in your browser automatically
autocue serve

# Custom port
autocue serve --port 8080

# Don't open the browser automatically
autocue serve --no-browser

# Windows / custom DB path
autocue serve --db-path "C:\Users\you\AppData\Roaming\Pioneer\rekordbox\master.db"
```

#### Requirements

- Python 3.10+
- Rekordbox 7 with tracks analyzed (BPM + phrase detection run)
- Rekordbox **closed** before clicking Apply (the database is locked while Rekordbox is open)
- macOS (DB path auto-detected); Windows requires `--db-path`

#### Safety

Before any write, AutoCue:
1. Checks that Rekordbox is not running (aborts with a clear error if it is)
2. Creates a timestamped backup at `~/.autocue/backups/master_TIMESTAMP.db`
3. Writes inside a SQLAlchemy savepoint — rolls back automatically on any failure

---

## Local server features

### Smart Cue Generation

AutoCue maps Rekordbox's own phrase labels to DJ-friendly names and assigns them to slots
in a predictable order:

| Rekordbox phrase | AutoCue cue name | Colour |
|---|---|---|
| Intro | Intro | Green |
| Up (high-energy) | Build | Pink |
| Chorus (high-energy) | Drop | Red |
| Verse | Verse | Blue |
| Down | Break | Purple |
| Bridge | Bridge | Cyan |
| Outro | Outro | Orange |

**Slot A is always the mix-in point** — the first non-Intro phrase boundary, where a DJ
would trigger the track during a transition. Slots B–H follow by musical importance:
Drop → Build → Outro → Verse → Break → Bridge → Intro.

Each cue badge shows a confidence level:
- **High** — phrase data present and beat-accurate
- **Medium** — bar-interval fallback (no phrase analysis)
- **Low** — heuristic estimate (no beat grid)

Click the **ℹ** icon on any cue badge to see exactly why AutoCue placed it there:

```
Cue Reasoning — High confidence
• Rekordbox phrase: Chorus (high-energy section)
• 8-bar phrase
• Priority slot: main drop
```

### Memory Cue (CDJ Auto Cue)

AutoCue can place a memory cue (the CDJ "Auto Cue" load position) alongside the hot cues.
Three modes are available in the UI:

| Mode | What gets a memory cue |
|---|---|
| None | No memory cue placed |
| Load point | One memory cue at the mix-in position (slot A) |
| All points | Load + Mix-In + Mix-Out memory cues |

The CDJ loads at the memory cue position when the track is loaded from a USB.

---

### Library Health Check

Scans your entire library and scores every track 0–100 based on cue readiness.
Streaming progress via SSE — a 10,000-track library completes in seconds.

**Health score formula:**

| Deduction | Condition |
|---|---|
| −30 | No hot cues at all (`NO_CUES`) |
| −10 | No Rekordbox phrase analysis (`NO_PHRASE`) |
| −10 | No beat grid (`NO_BEATGRID`) |
| −5 | Duplicate cues within 10ms of each other (`DUPLICATE_CUE`) |
| −5 | Unnamed cues or cues named "Cue 1" etc. (`UNNAMED_CUES`) |
| =0 | Audio file missing from disk (`NO_AUDIO_FILE`) — track is dead weight |

**Fix tiers** tell you what quality of auto-fix is possible per track:

| Tier | Condition | Confidence |
|---|---|---|
| `phrase` | Phrase data + beat grid present | 1.0 — phrase-accurate |
| `bar` | Beat grid only, no phrases | 0.6 — bar-interval |
| `heuristic` | No beat grid | 0.3 — position estimate |
| `none` | Audio file missing | — cannot generate |

**Library health report:**
```
Library Health: 78/100  (3,389 tracks)

  ✗ 142 tracks have no hot cues
      → 98 fixable at phrase quality
      → 32 fixable at bar quality
      → 12 fixable at heuristic quality
  ✗  12 tracks — audio file missing [excluded from score]
  ℹ 203 tracks have no phrase analysis   [Re-analyze in Rekordbox]
  ℹ  34 tracks have duplicate cues
  ✓ 3389 tracks: OK
```

Click **Fix phrase-quality tracks** to batch-generate cues for all fixable tracks in one step.
Filter by playlist first to rescan only tracks you've just analyzed in Rekordbox.

---

### Energy Curve

Each track card shows an **energy sparkline** — a mini waveform derived directly from
Rekordbox's PWAV analysis data (the same data it uses to draw the waveform overview strip).
No additional audio processing is required.

The sparkline shows one of four profiles:
- `flat` — consistent energy throughout
- `build` — energy rises toward the end
- `drop-then-flat` — peaks then stabilises (classic EDM structure)
- `wave` — multiple energy peaks and valleys

---

### Mixability Score

Every track gets a **Mixability score (0–100)** showing how easy it is to mix in and out of.
Shown as a chip on each track card (e.g. `Mix 72/100`).

**Formula:**

| Component | Weight | What it measures |
|---|---|---|
| Intro bars | 25% | Bars available at the start for mixing in (32+ bars = perfect) |
| Outro bars | 25% | Bars available at the end for mixing out |
| Energy consistency | 20% | Low variance = steady energy = easier to mix |
| Vocal density | 15% | Sparse/no vocals = more room to blend tracks |
| Phrase structure | 15% | More distinct phrases = more mix points available |

Scores are calibrated by genre: Techno (140+ BPM) typically scores 70–80,
House (120–130 BPM) 60–70, open-format vocal tracks 30–50. This reflects
actual mixability, not track quality.

---

### Track Classification

Each track is automatically classified into one of five DJ categories, shown as a badge
on its card:

| Category | BPM range | Energy | Vocals |
|---|---|---|---|
| `warmup` | 90–120 | Low | Any |
| `build` | 118–128 | Medium | No preference |
| `peak` | 126–145 | High | Sparse preferred |
| `after_hours` | 100–122 | Low–medium | OK |
| `closing` | 70–118 (peaks at 88) | Low | Any |

Tracks can score in multiple categories — the badge shows the primary category. Used by
Similar Track Discovery, Playlist Suggest, and Set Builder.

---

### Similar Track Discovery

Click **≈ Similar** on any track card to see the 5 most similar tracks in your library
within ±8 BPM.

Similarity is calculated using a 5-dimensional feature vector:
- Key (cos/sin encoded Camelot position)
- Energy mean + variance
- Vocal proxy (has Verse phrases?)

All 3,000–50,000 tracks are held in a memory index (~3 MB). No cloud service, no API key.
Similarity lookup is near-instant.

---

### Transition Scoring

Score the compatibility of any two tracks for mixing.

**Score components:**

| Component | Weight | Details |
|---|---|---|
| BPM compatibility | 40% | 100 if within ±3%, falls to 0 at ±15%. Half-time/double-time aware. |
| Key compatibility | 35% | Camelot wheel: same=100, adjacent number=80, same number diff letter=75, ±2=50 |
| Energy compatibility | 25% | How well the end energy of track A matches the start energy of track B |

Each score includes a human-readable explanation:
```json
{
  "overall": 88.5,
  "bpm": 97,
  "key": 80,
  "energy": 78,
  "explanation": [
    "BPM: 120.0 → 121.5 (1.2% difference) — excellent",
    "Key: 8A → 8B (parallel minor) — good",
    "Energy: 0.62 → 0.58 — smooth"
  ]
}
```

---

### Set Builder

Build a full DJ set automatically. Given a start BPM, end BPM, duration, and energy mode,
AutoCue assembles a set using beam search over your library.

**Inputs:**
- Start BPM / End BPM — defines the BPM arc for the set
- Duration (minutes) — target set length
- Energy mode — `Build (ascending)`, `Drop (descending)`, or `Flat`

**Algorithm:**
1. Find a seed track near the start BPM in the target category
2. For each step, fetch the 20 most similar candidates (BPM-gated)
3. Score each candidate with transition scoring — filter below 40/100
4. Apply an energy penalty for candidates that contradict the energy mode
5. Beam search (width=5) explores the 5 best paths simultaneously
6. Returns the highest-scoring complete path

**Example output:**
```
4 tracks · ~21.9 min

1. vocals             112.6 BPM  5A  warmup   —
2. juno synth synth   119.8 BPM  5A  warmup   81
3. bell               127.7 BPM  5A  build    80
4. tik tik            127.7 BPM  5A  build   100
```

Each track shows its transition score from the previous track. No track appears twice.

---

### Cue Library Tools

Bulk-edit cues across your entire library (or just the visible/filtered tracks).
All operations stream progress via SSE and create a backup before any write.

**Operations:**

| Operation | What it does |
|---|---|
| **Rename cues** | Replace an exact cue name across all tracks (e.g. "Cue 1" → "Drop") |
| **Recolor cues** | Set a specific slot's color on all tracks |
| **Shift cues** | Move all cues by ±N milliseconds (e.g. to correct a systematic beatgrid offset). Updates both in-point and loop out-point. |
| **Delete orphan cues** | Remove all cues in slots above a specified slot number |

**Safety:**
- **Dry run (preview) is on by default** — see affected count before any write
- Destructive operations (shift, delete) require an explicit confirmation dialog
- A backup of `master.db` is created before every write
- Rekordbox must be closed before any write operation

---

### Playlist Suggest

Get track recommendations filtered by DJ category. Select a category (warmup, build,
peak, after_hours, closing) and a count, and AutoCue returns the best-matching tracks
from your library sorted by category score.

---

## Python CLI

Reads directly from your Rekordbox database. Outputs a Rekordbox XML file for import.

#### Install

```bash
git clone https://github.com/HenriGeorge/AutoCue.git
cd AutoCue
pip install -e .
```

#### Usage

```bash
# Process all tracks (auto-selects best strategy per track)
autocue --library

# Preview without writing any files
autocue --library --dry-run

# Single track by title
autocue --track "Song Title"

# Single track by Rekordbox ID
autocue --track-id 42

# Process a specific playlist
autocue --library --playlist "My Set"

# Re-generate cues even for tracks that already have them
autocue --library --overwrite

# Custom output file
autocue --library --output my_cues.xml

# Windows / custom DB path
autocue --library --db-path "C:\path\to\master.db"
```

Output is a Rekordbox XML file. Import it in Rekordbox via **File → Import Library**.

#### Cue placement strategy (auto mode)

The CLI tries three strategies in order, using the best available data:

| Strategy | Used when | Cue positions | Cue names |
|---|---|---|---|
| **Phrase** | Rekordbox phrase analysis available | At phrase boundaries | Intro, Verse, Drop, Build, Break, Bridge, Outro (numbered if repeated: "Drop 1", "Drop 2") |
| **Bar** | BPM known, no phrase data | Every 16 bars from bar 1 | Bar 1, Bar 17, Bar 33… |
| **Heuristic** | No BPM or phrase data | Every 30 seconds | 0:00, 0:30, 1:00… |

---

## How cue placement works

### Bar intervals

Uses the `Inizio` (first beat offset) and `BPM` from the Rekordbox XML:

```
barDuration = (60 / BPM) × beatsPerBar
cue[i]      = Inizio + (startBar − 1 + i × barsInterval) × barDuration
```

Default: 8 cues every 16 bars from bar 1 → bars 1, 17, 33, 49, 65, 81, 97, 113.

### Phrase analysis

Uses Rekordbox's own phrase detection stored in ANLZ files on disk:

- `.EXT` → `PSSI` tag: phrase boundaries with type (Intro, Verse, Chorus, Outro…) and mood
- `.DAT` → `PQTZ` tag: beat grid — exact millisecond timestamp per beat
- `.DAT` → `PWAV` tag: waveform overview — used for energy curve and mixability score

**Smart slot ordering** ensures Slot A is always the DJ mix-in point (first non-Intro phrase),
with remaining slots assigned by musical importance: Drop → Build → Outro → Verse → Break.

---

## REST API reference

The local server exposes a full REST API at `http://localhost:7432`.

#### Core

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/status` | Server info + DB path + diagnostic header |
| GET | `/api/config` | Runtime config (download dir, Discogs token presence, …) |
| GET | `/api/warmup` | Warm-up progress (cache hydrate → similar index → done) |
| GET | `/api/perf/recent` | Perf ring buffer (dev-only — 404 unless `AUTOCUE_PERF=1`) |
| GET | `/api/tags` | All My Tags |
| GET | `/api/playlists` | All playlists |
| POST | `/api/playlists` | Create playlist |
| POST | `/api/playlists/suggest` | Suggest tracks for a DJ category |

#### Tracks

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/tracks` | Tracks (`?playlist_id=N`, ETag/304, optional NDJSON streaming) |
| GET | `/api/tracks/{id}/artwork` | Track artwork |
| GET | `/api/tracks/{id}/audio` | Track audio stream |
| GET | `/api/tracks/{id}/energy` | Energy curve (PWAV-derived, normalized 0–1) |
| GET | `/api/tracks/{id}/mixability` | Mixability score (0–100) + components |
| GET | `/api/tracks/{id}/classification` | Category scores (warmup/build/peak/…) |
| GET | `/api/tracks/{id}/similar` | Similar tracks (`?n=10&bpm_gate=8&force_rebuild=false`) |
| GET | `/api/tracks/{id}/health` | Single-track cue quality report |
| POST | `/api/tracks/check-audio` | Bulk check whether audio files exist on disk |

#### Cues — write paths (Rekordbox must be closed)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/generate` | Generate cue preview (no write) |
| POST | `/api/apply` | Apply cues to Rekordbox DB |
| POST | `/api/generate-apply` | Generate + apply in one step |
| POST | `/api/generate-apply-stream` | SSE — generate + apply in one step |
| POST | `/api/delete-cues` | Delete all cues for given track IDs |
| POST | `/api/color-tracks` | Color tracks by BPM range |
| POST | `/api/color-tracks-stream` | SSE — color tracks by BPM range |
| POST | `/api/cue-tools-stream` | SSE — bulk rename / recolor / shift / delete cues |

#### Library intelligence

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | SSE — library-wide health scan (`?playlist_id=N&limit=N`) |
| GET | `/api/duplicates` | SSE — duplicate-track scan, groups by (artist, title) and picks a keeper. Read-only |
| POST | `/api/duplicates/delete` | Delete N tracks identified by the scan. Rekordbox-closed guard + backup before write |
| GET | `/api/classify` | SSE — library-wide classification (`?playlist_id=N`) |
| POST | `/api/transitions/score` | Transition score for two tracks |
| POST | `/api/setbuilder` | Build a DJ set by beam search |
| GET | `/api/setbuilder/alternatives` | Replacement candidates for a set slot |

#### Auto-tag + comments (My Tags / DjmdContent.Commnt)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auto-tag` | Apply detector-driven My Tags |
| POST | `/api/auto-tag/undo` | Reverse an auto-tag run |
| POST | `/api/auto-tag/discogs/test` | Test Discogs token + lookup for one track |
| POST | `/api/auto-tag/discogs` | SSE — Discogs-driven genre/style tagging |
| POST | `/api/enrich-comments` | Apply MIK-compatible comment enrichment |
| POST | `/api/enrich-comments/preview` | Preview the comment string per track |
| POST | `/api/enrich-comments/stream` | SSE — comment enrichment with progress |
| POST | `/api/enrich-comments/undo` | Reverse a comment enrichment run |

#### Discover v2 (new-release surfacing)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/discover` | SSE — legacy Discogs new-release suggestions |
| GET | `/api/discover/feed` | Discover v2 feed (artist / label / novelty) |
| GET | `/api/discover/feed/status` | Active scan status |
| POST | `/api/discover/feed/cancel` | Cancel running scan |
| GET | `/api/discover/stats` | Taste vector + feed stats |
| GET | `/api/discover/token-status` | Discogs token health |
| GET | `/api/discover/releases/{release_id}` | Single release detail |
| GET, POST | `/api/discover/labels`, `/api/discover/labels/search`, `/api/discover/labels/suggested`, `/api/discover/labels/follow`, `/api/discover/labels/unfollow` | Followed-label management |
| POST | `/api/discover/save`, `/api/discover/unsave`, `/api/discover/dismiss`, `/api/discover/undismiss`, `/api/discover/snooze`, `/api/discover/unsnooze`, `/api/discover/block-artist`, `/api/discover/unblock-artist`, `/api/discover/block-label`, `/api/discover/unblock-label` | Per-release / per-entity state changes (snooze = 1w/1m/3m only) |
| GET | `/api/discover/saved`, `/api/discover/snoozed`, `/api/discover/dismissed`, `/api/discover/downloaded`, `/api/discover/blocked-artists`, `/api/discover/blocked-labels` | State listings |
| GET, POST | `/api/discover/state/export`, `/api/discover/state/import` | Backup + restore Discover state |

#### YouTube download (optional — requires `[download]` extra + ffmpeg)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/download/config` | yt-dlp / ffmpeg availability + default dir |
| GET | `/api/youtube/search` | Candidate picker for ambiguous queries |
| GET | `/api/download/queue` | Active + queued jobs |
| GET | `/api/download/stream/{job_id}` | SSE — per-job progress |
| POST | `/api/download` | SSE — single-track download |
| POST | `/api/download/album` | SSE — album download |
| POST | `/api/download/enqueue`, `/api/download/album/enqueue` | Enqueue for background worker |
| POST | `/api/download/cancel/{job_id}` | Cancel a running / queued job |
| POST | `/api/download/reveal` | Reveal downloaded file in Finder / Explorer |

#### Backups

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/backups` | List available backups |
| POST | `/api/restore` | Restore a backup (invalidates the sidecar cache) |
| DELETE | `/api/backups/{filename}` | Delete a backup |

---

## Development

```bash
pip install -e ".[dev]"              # install with test deps
pytest                               # run all 1427 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 633 Vitest tests for the web app

autocue serve --no-browser           # start local server without opening browser
autocue --library --dry-run          # preview CLI output without writing
```

**Project layout:**

```
autocue/
  models.py        — PhraseLabel enum + CuePoint dataclass
  analyzer.py      — ANLZ phrase (PSSI) + beat grid (PQTZ) parser
  generator.py     — phrase → bar → heuristic strategy; smart slot ordering; confidence scores
  writer.py        — writes CuePoints to Rekordbox XML
  db_writer.py     — writes CuePoints to DjmdCue; backup + safety checks; BPM coloring
  cli.py           — argparse CLI; `autocue serve` subcommand
  cache.py         — Sidecar SQLite cache at <rekordbox_dir>/autocue_cache.sqlite (L2)
  cache_reset.py   — `autocue serve --reset-cache` implementation
  perf.py          — perf_span() context manager + ring buffer (AUTOCUE_PERF=1)
  download.py      — Optional yt-dlp wrapper (requires [download] extra + ffmpeg)
  analysis/
    concurrency.py  — Shared process-singleton ThreadPoolExecutor (AUTOCUE_POOL_SIZE)
    anlz_path.py    — Single source of truth for ANLZ mtime cache keys
    quality.py      — Cue Quality Checker: health score 0–100, fix tiers, SSE streaming
    energy.py       — PWAV waveform reader → energy curve + profile classifier
    score.py        — Mixability score (0–100): intro/outro/energy/vocal formula
    classify.py     — Track classification (warmup/build/peak/after_hours/closing)
    similar.py      — 6-dim cosine similarity index with ±8 BPM gate
    transitions.py  — BPM (40%) + Key Camelot wheel (35%) + Energy (25%) scoring
    setbuilder.py   — Beam search set builder (width=5, energy mode, deduplication)
    auto_tag.py     — Writes DJ analysis as Rekordbox My Tags; undo support
    comment.py      — Track comment enrichment → DjmdContent.Commnt (MIK-compatible)
    discogs.py      — Discogs API client (rate-limited; styles + recent releases)
    discovery.py    — Legacy new-release suggestions (Discogs-based)
    discover/       — Discover v2: taste, style_graph, feeders/, ranker,
                       scan_orchestrator, store; new-release surfacing with novelty rotation
  serve/
    app.py          — FastAPI app factory + uvicorn launcher
    middleware.py   — Snapshot-invalidation middleware (2xx POST/PUT/DELETE under /api/*)
    routes.py       — All API endpoints
    schemas.py      — Pydantic request/response models
    deps.py         — DB connection lifecycle + L2 wiring + warm-up pipeline

docs/
  index.html        — Single-file web app (no build step, no framework, no dependencies)
  FEATURES.md       — End-user feature documentation
  reference/        — Per-feature reference docs (rest-api, discover-v2, set-builder, …)
  guides/           — Static DJ learning guides

tests/
  test_*.py         — 1378 Python tests covering CLI, generator, writer, db_writer,
                      analysis modules, perf, cache, snapshot, serve routes, and
                      Discover v2 (taste, style_graph, feeders, ranker, orchestrator, store)
  e2e/              — Playwright smoke harness (autocue-qa agent) — sandbox port-0 server
  perf/             — Perf-budget tests (gated by RUN_PERF=1; `make perf`)
  web/              — 579 Vitest tests across 30 spec files (xml-processing, ui-logic,
                      Discover v2 onboarding/snooze/download/integration/sort/stats/…,
                      virtualizer, sticky structure, UX PRs, warmup badge, perf helper)
```

---

## Prior art

- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) — Python library for the Rekordbox database
- [djcues](https://github.com/mcroydon/djcues) — automated cue placement
- [CueGen](https://github.com/mganss/CueGen) — Mixed In Key to Rekordbox cues

## License

MIT
