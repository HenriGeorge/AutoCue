**CueBadge** — a single hot-cue chip. Color comes from the slot (0–7 → A–H palette); `confidence` dims bar (.70) and heuristic (.45) cues exactly like the app.

```jsx
<CueBadge slot={0} label="Drop (Mix In)" playable />
<CueBadge slot={1} label="Verse" confidence="bar" />
<CueBadge slot={5} label="0:30" confidence="heuristic" />
```
