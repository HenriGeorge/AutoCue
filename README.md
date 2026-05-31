# AutoCue

Automatically place hot cues on every track in your Rekordbox 7 library.

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

Each phrase type gets a distinct colour in Rekordbox:

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
- The output XML only replaces cue slots AutoCue uses — manually placed cues in other slots survive the import unchanged.
- A conflict warning highlights tracks where existing hot cues will be overwritten.

---

## Local server (`autocue serve`)

The fastest end-to-end workflow. Run a local server that reads your Rekordbox database directly — no XML export, no XML import.

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

The browser UI loads your full library directly from Rekordbox. Click **Apply to Rekordbox** to write cues in one step — no import needed.

#### Requirements

- Python 3.10+
- Rekordbox 7 with tracks analyzed
- Rekordbox **closed** before clicking Apply (the database is locked while Rekordbox is open)
- macOS (DB path auto-detected); Windows requires `--db-path`

#### Safety

Before any write, AutoCue:
1. Checks that Rekordbox is not running (aborts with a clear error if it is)
2. Creates a timestamped backup of `master.db` at `~/.autocue/backups/master_TIMESTAMP.db`
3. Writes inside a SQLAlchemy savepoint — rolls back automatically on any failure

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

The CLI tries three strategies in order, using the best available data for each track:

| Strategy | Used when | Cue positions | Cue names |
|---|---|---|---|
| **Phrase** | Rekordbox phrase analysis available | At phrase boundaries | Intro, Verse, Drop, Build, Break, Bridge, Outro (numbered if repeated: "Drop 1", "Drop 2") |
| **Bar** | BPM known, no phrase data | Every 16 bars from bar 1 | Bar 1, Bar 17, Bar 33… |
| **Heuristic** | No BPM or phrase data | Every 30 seconds | 0:00, 0:30, 1:00… |

The strategy used is shown per-track in the output summary.

#### Requirements

- Python 3.10+
- Rekordbox 7 with tracks analyzed (BPM + phrase detection run in Rekordbox)
- Rekordbox **closed** before running (the database is locked while Rekordbox is open)
- macOS (default DB path is auto-detected); Windows requires `--db-path`

---

## How cue placement works

### Bar intervals

Uses the `Inizio` (first beat offset in seconds) and `BPM` from the `<TEMPO>` element in your Rekordbox XML export:

```
barDuration = (60 / BPM) × beatsPerBar
cue[i]      = Inizio + (startBar − 1 + i × barsInterval) × barDuration
```

Default settings: 8 cues every 16 bars from bar 1 → cues at bars 1, 17, 33, 49, 65, 81, 97, 113.

### Phrase analysis

Uses Rekordbox's own phrase detection, stored in binary ANLZ files on disk:

- `.EXT` → `PSSI` tag: phrase boundaries with type (Intro, Verse, Chorus, Outro, Bridge…) and mood
- `.DAT` → `PQTZ` tag: beat grid — exact timestamp in milliseconds per beat

**Two-pass algorithm** ensures structurally important sections always get a slot:

1. **Pass 1** — first occurrence of each unique phrase type (guarantees Intro, Chorus, Outro each get a cue even in a track dominated by Verse)
2. **Pass 2** — fill remaining slots (up to 8 total) with other phrase boundaries in chronological order

Cues are assigned to hot cue slots A–H in chronological order.

---

## Development

```bash
pip install -e ".[dev]"              # install with test deps
pytest                               # run all 184 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 65 Vitest tests for the web app

autocue serve --no-browser           # start local server without opening browser
autocue --library --dry-run          # preview CLI output without writing
```

---

## Prior art

- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) — Python library for the Rekordbox database
- [djcues](https://github.com/mcroydon/djcues) — automated cue placement
- [CueGen](https://github.com/mganss/CueGen) — Mixed In Key to Rekordbox cues

## License

MIT
