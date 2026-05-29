# AutoCue

Automatically place hot cues on every track in your Rekordbox 7 library.

**[Try the web app →](https://henrigeorge.github.io/AutoCue/)**
&nbsp;·&nbsp;
[Python CLI](#python-cli)

---

## Two ways to use

### Web app (no install)

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

#### Backup & safety

- **Download backup XML** saves your original file before any changes.
- The output XML only replaces cue slots AutoCue uses — manually placed cues in other slots survive the import unchanged.
- A conflict warning highlights tracks where existing hot cues will be overwritten.

---

### Python CLI

Reads directly from your Rekordbox database. Phrase analysis runs automatically — no need to export XML first.

#### Install

```bash
pip install pyrekordbox
git clone https://github.com/HenriGeorge/AutoCue.git
cd AutoCue
pip install -e .
```

#### Usage

```bash
# Process all tracks that have Rekordbox phrase analysis
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

#### Requirements

- Python 3.10+
- Rekordbox 7 with tracks analyzed (BPM + phrase detection run in Rekordbox)
- Rekordbox **closed** before running (the database is locked while Rekordbox is open)
- macOS (default DB path is auto-detected); Windows requires `--db-path`

---

## How cue placement works

### Bar intervals (web app default)

Uses the `Inizio` (first beat offset in seconds) and `BPM` from the `<TEMPO>` element in your Rekordbox XML export:

```
barDuration = (60 / BPM) × beatsPerBar
cue[i]      = Inizio + (startBar − 1 + i × barsInterval) × barDuration
```

Default settings: 8 cues every 16 bars from bar 1 → cues at bars 1, 17, 33, 49, 65, 81, 97, 113.

### Phrase analysis (CLI + web app phrase mode)

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
pip install -e ".[dev]"
pytest                   # 86 tests
```

---

## Prior art

- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) — Python library for the Rekordbox database
- [djcues](https://github.com/mcroydon/djcues) — automated cue placement
- [CueGen](https://github.com/mganss/CueGen) — Mixed In Key to Rekordbox cues

## License

MIT
