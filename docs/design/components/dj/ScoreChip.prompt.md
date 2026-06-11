**ScoreChip** — the green outline `NN/100` chip used for mixability, health, and transition scores. `null` value → muted no-data chip.

```jsx
<ScoreChip value={72} />                 {/* Mix 72/100 */}
<ScoreChip label="Health" value={78} />
<ScoreChip value={null} />               {/* Mix — (no data) */}
```
