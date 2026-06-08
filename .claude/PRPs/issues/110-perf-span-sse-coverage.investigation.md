# Issue #110 — TASK-046 perf_span coverage misclaimed

## Problem
TASK-046 (Performance PRD §4.7) was marked `passes: true` in `.agent/tasks.performance.json` but
only two `perf_span` sites landed in `autocue/serve/routes.py` (`tracks.cached` and `tracks.build`).
Every SSE endpoint in PRD §4.1 — generate-apply, color-tracks, library-health, cue-tools, classify,
auto-tag-discogs, enrich-comments — has zero instrumentation, which means `/api/perf/recent` returns
useful data only for `/api/tracks` and the PRD §6 throughput targets are unmeasurable.

## Root cause
PR #74 (`feat(perf): TASK-046 + TASK-047 + TASK-050 — perf spans + perf suite + frontend perf marks`)
landed at commit `d9888d0` only wrapped the `/api/tracks` paths. The TASK-046 spec at
`.agent/tasks/performance/TASK-046.json` step 2 explicitly required
`perf_span(f'{endpoint}.compute')` outer + per-track inner spans on every SSE endpoint in PRD §4.1.

## Proposed solution
Add `perf_span` instrumentation to the six §4.1 SSE endpoints with the naming convention
`<endpoint>.compute` (outer total) and `<endpoint>.write_one` (per-track write commit). Use the
existing `_perf` import in `autocue/serve/routes.py`. Per the TASK-046 technical-note,
spans go around per-track and per-request boundaries — not inside the cue array loop.

Spans added:
- `generate_apply.compute` (outer) + `generate_apply.write_one` (per-track writer commit)
- `color_tracks.compute` (outer) + `color_tracks.write_one` (per-track update execute)
- `library_health.compute` (outer; no writer — read-only)
- `cue_tools.compute` (outer) + `cue_tools.write_one` (per-track mutation block)
- `classify.compute` (outer)
- `auto_tag_discogs.compute` (outer) + `auto_tag_discogs.write_one` (per-track tag write+commit)
- `enrich_comments.compute` (outer) + `enrich_comments.write_one` (per-track Commnt commit)

Sampling is already handled in `autocue/perf.py` via `AUTOCUE_PERF_SAMPLE_RATE`. We do NOT
change the default sample rate in this fix — the PRD's "1-in-10" is an operational tuning,
not a code-default; setting `AUTOCUE_PERF_SAMPLE_RATE=0.1` is the documented switch.
Default stays at 1.0 (sample everything when AUTOCUE_PERF=1), matching existing behavior.

## Affected files
- `autocue/serve/routes.py` — add `_perf.perf_span` calls in six SSE endpoints.
- `tests/test_perf_endpoint.py` — add a regression test that exercises one SSE endpoint with
  `AUTOCUE_PERF=1` and asserts the expected span names appear in `recent_spans()`.

## Risks
- Spans inside the per-track loop add nanoseconds of overhead even when disabled (one `if not _enabled`
  branch per call). Negligible — proven by existing `tracks.build` / `tracks.cached` instrumentation.
- The instrumentation must not change endpoint behavior — pure observation. We add `with` blocks
  around existing logic; no control-flow changes.
- We do NOT modify any control flow inside `event_stream` generators that could affect the
  SSE event order or content (TASK-042 invariant).
