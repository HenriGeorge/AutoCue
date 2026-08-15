Last updated: 2026-06-11 13:36

**Toggle** — small pill switch; the track turns `--green` when on. Controlled.

```jsx
const [on, setOn] = React.useState(true);
<Toggle checked={on} onChange={setOn} label="Skip already colored" />
```
