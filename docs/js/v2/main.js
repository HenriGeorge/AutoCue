/**
 * AutoCue 2.0 — ES-module seam (P0 T6).
 *
 * ALL new v2 code lands here as native ES modules (import/export), loaded via
 * <script type="module"> — no bundler, ever. Legacy code lives in classic
 * scripts (docs/js/app.js and its future feature-file splits) sharing globals;
 * it migrates into modules opportunistically, never wholesale.
 *
 * Interop rules:
 * - v2 modules may READ legacy globals via `window.*` (e.g. window.showToast).
 * - Legacy code must NOT reach into v2 modules; v2 attaches any surface it
 *   exposes to `window.AC2` explicitly.
 * - This file is the only module entry point; new features are imports here.
 *
 * Program PRD: .claude/PRPs/prds/autocue-2-program.prd.md
 */

window.AC2 = window.AC2 || {};

// P1 global layer.
import { initStatusSentence, deriveFacts } from './status-sentence.js';
window.AC2.statusSentence = { initStatusSentence, deriveFacts };

import { openPalette, closePalette, isOpen, buildResults, clampActive } from './palette.js';
window.AC2.palette = { openPalette, closePalette, isOpen, buildResults, clampActive };

// P2 workbench shell (flag-gated, additive).
import { initWorkbench, toggleWorkbench, isWorkbenchOn, setWorkbench } from './workbench/shell.js';
window.AC2.workbench = { initWorkbench, toggleWorkbench, isWorkbenchOn, setWorkbench };
