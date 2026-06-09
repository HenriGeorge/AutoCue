# Issue #114 — album→flat sort leaves stale `.album-group` DOM under the Virtualizer

## Problem

Switching the Cues-tab sort from **Album** to any flat-mode key (Title, BPM, Key, Plays, etc.) does not clear the previously rendered album-group DOM before `Virtualizer.attach()` runs. The Virtualizer's `attach()` only appends its spacer — it does not empty the container — so the album-group subtree (305 groups, 3,791 cached `.track-card` nodes) survives and is rendered behind the recycled flat-mode window. This defeats the bounded-DOM goal of the Virtualizer (PR #99 / TASK-032).

Verified DOM probe on the live UI after `album → title`:

```
children=322, first_child_class=.album-group,
track_cards=3791, album_groups=305, virt_attached=true,
doc_height=633853
```

Expected after switching to flat sort: virtualizer-owned DOM only (~16 visible cards + spacer; no `.album-group` nodes).

## Root cause

`docs/index.html` — flat-mode branch of `renderTrackList` (line ~10522).

The album-mode branch (line 10422) does `list.innerHTML = ''` whenever it rebuilds. The flat-mode branch never does, because `Virtualizer.detach()` only removes nodes that the Virtualizer itself mounted (its `live` map, its `pool`, and its `spacer`). It cannot know about the orphan album-group children left over from the previous album-mode render.

The album-mode branch also caches its built cards into `_cardMap`. Once we destroy the album-group DOM, every entry in `_cardMap` is a detached node — the flat branch never reads `_cardMap` (`_cardMap.size > 0 && !Virtualizer.isAttached()` guard at line 10712 disqualifies it), but holding 3.8k detached DOM nodes alive defeats the memory win for the rest of the session, so the cache must be cleared too.

## Proposed solution

In `docs/index.html` :: `renderTrackList`, immediately at the top of the flat-mode branch (the `else` of `currentSort.by === 'album'`), drop the album-mode DOM when the previous render was album-grouped:

```js
// Album-mode DOM (built by the `if (currentSort.by === 'album')` branch above)
// is invisible to Virtualizer.attach() — it would render the flat window on top
// of the orphan album-group children and never recover the memory. Clear it
// explicitly on the album → flat transition. (Issue #114.)
if (list.querySelector('.album-group')) {
  list.innerHTML = '';
  _cardMap.clear();
}
```

## Affected files

- `docs/index.html` — 5-line fix in the flat-mode branch of `renderTrackList`.
- `tests/web/album-to-flat-stale-dom.test.js` (NEW) — regression test that:
  1. Pre-populates a `#track-list` with `.album-group` children (album-mode shape).
  2. Calls the same cleanup helper before attaching the Virtualizer.
  3. Asserts `.album-group` count is 0 and that only Virtualizer-owned children remain after attach.
  4. Boundary case: an empty container (no prior album render) must NOT have its `_cardMap` cleared unnecessarily — the `querySelector` guard ensures the no-op path.

## Risks

- **None for album → album re-sort or filter changes** — the album-mode branch keeps its own `list.innerHTML = ''` path on `settingsChanged || orderChanged || !list.firstChild`.
- **None for flat → flat re-sort** — there are no `.album-group` nodes after the first transition, so the guard is a single `querySelector` no-op (~µs).
- **None for memory** — `_cardMap.clear()` only releases references; the nodes are already detached when `list.innerHTML = ''` runs immediately above.
- **Virtualizer.detach() is already called inside the album-mode branch** (line 10400) when switching back to album mode, so this fix is symmetric.
