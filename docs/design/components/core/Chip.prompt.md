**Chip** — interactive pill for filters, sort buttons, genre tags. Active state is neutral by default (sort buttons reserve green for signal); use `accent="green"` for filter chips that should glow green when on.

```jsx
<Chip active>BPM ↑</Chip>           {/* neutral active — like sort buttons */}
<Chip>Key</Chip>
<Chip accent="green" active>Techno</Chip>  {/* green active — like genre chips */}
```
