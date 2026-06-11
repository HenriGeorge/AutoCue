**Badge** — small rounded status/label pill for inline metadata and states.

```jsx
<Badge tone="green">OK</Badge>
<Badge tone="warn" variant="outline">No phrase data</Badge>
<Badge tone="danger">Audio missing</Badge>
<Badge>3 tracks</Badge>
```

Tones: `neutral` (default), `green`, `danger`, `warn`. Variants: `soft` (tinted wash bg) or `outline`. Always a pill; 10px/600 text. Use for short statuses — for measured DJ data use `BpmChip`/`KeyChip`/`ScoreChip` instead.
