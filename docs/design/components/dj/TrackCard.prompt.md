**TrackCard** — the core AutoCue library row. Composes artwork, title/artist, `BpmChip` + `KeyChip` + time, a Rekordbox color dot/left-border, hot-cue `CueBadge`s, an `EnergySparkline`, a mixability `ScoreChip`, and a `CategoryBadge`. Hover lifts the card.

```jsx
<TrackCard track={{
  title: 'Strobe', artist: 'deadmau5', bpm: 128, keyName: '4A', time: '10:34', color: 'Blue',
  cues: [
    { slot: 0, label: 'Drop (Mix In)' },
    { slot: 1, label: 'Verse' },
    { slot: 5, label: 'Outro', confidence: 'bar' },
  ],
  energy: [0.1,0.2,0.4,0.7,0.9,0.85,0.95,0.7,0.5,0.3],
  mix: 74, category: 'build',
}} />
```

Pass only the fields you have — chips and rows render conditionally.
