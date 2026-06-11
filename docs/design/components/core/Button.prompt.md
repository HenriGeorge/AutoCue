**Button** — AutoCue's fully-rounded pill button; use for every action. The primary CTA is the ink pill (black on light / white on dark), *not* green — green is reserved for signal/brand.

```jsx
<Button variant="primary">Apply to Rekordbox</Button>
<Button variant="secondary">Preview cues</Button>
<Button variant="ghost" size="sm">Clear</Button>
<Button variant="danger">Delete non-keepers</Button>
```

Variants: `primary` (ink pill), `secondary` (surface + border + soft shadow), `ghost` (transparent, border brightens on hover), `danger` (red outline). Sizes `sm` / `md`. Pass `icon` for a leading 14–16px inline SVG. Hover lifts (`translateY(-1px)`); all variants are pills (`--radius-pill`).
