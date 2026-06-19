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

// P2 workbench proposal organ — proposal stamps + per-track approve ticks +
// approved∩pending Apply gating. Owns its own click-delegation init (capture
// phase) so it must run after #track-list exists; main.js loads at end of body.
import { initProposals } from './workbench/proposals.js';
initProposals();

// P3 Duplicates place — rail entry that swaps the workbench centre pane to
// the duplicates view. Delegation-only: drives the legacy duplicates
// machinery via ACBridge; owns the door + the swap, nothing else.
import { initDuplicatesPlace, activate as activateDuplicates, deactivate as deactivateDuplicates, isActive as duplicatesActive } from './workbench/duplicates.js';
window.AC2.duplicates = { activate: activateDuplicates, deactivate: deactivateDuplicates, isActive: duplicatesActive };
initDuplicatesPlace();

// P3 (R8) — restore sheet: the canonical A-layer undo off the status sentence,
// fed by the autocue:duplicates-deleted event the legacy delete path dispatches.
import { initRestoreSheet } from './restore-sheet.js';
initRestoreSheet();

// P5 Discover place — rail entry that swaps the workbench centre pane to the
// restyled Discover feed (via switchTab). Delegation-only: re-drives the legacy
// DiscoverV2 IIFE via window.DiscoverV2 / ACBridge.discover; owns the door + the
// swap + the inspector re-host, nothing else. Mutually exclusive with Duplicates.
import { initDiscoverPlace, activate as activateDiscover, deactivate as deactivateDiscover, isActive as discoverActive, focusRelease as discoverFocusRelease } from './workbench/discover.js';
window.AC2.discover = { activate: activateDiscover, deactivate: deactivateDiscover, isActive: discoverActive, focusRelease: discoverFocusRelease };
initDiscoverPlace();

// Library place — the LAST surface off the legacy #tab-nav tab bar. Mirrors the
// Discover place (own tab-content block, shown via switchTab('library')); moving
// it into the rail lets the Cues/Library tab bar be retired.
import { initLibraryPlace, activate as activateLibrary, deactivate as deactivateLibrary, isActive as libraryActive } from './workbench/library.js';
window.AC2.library = { activate: activateLibrary, deactivate: deactivateLibrary, isActive: libraryActive };
initLibraryPlace();

// P4 Nightboard — full-bleed set-builder canvas MODE (not a centre-pane place):
// hides rail+grid+inspector and owns the body, keeping the global topbar. Built
// on the existing setbuilder/transitions REST surface (no new analysis, no new
// endpoint). Entered via the workbench toolbar verb + a ⌘K command.
import { initNightboard, openNightboard, closeNightboard, isNightboardOpen } from './nightboard/mode.js';
import { initJointPopover, close as closeJointPopover, isOpen as jointPopoverOpen } from './nightboard/joint-popover.js';
import { initTray, focusTile, clearFocus } from './nightboard/tray.js';
window.AC2.nightboard = { open: openNightboard, close: closeNightboard, isOpen: isNightboardOpen, closePopover: closeJointPopover, popoverOpen: jointPopoverOpen, focusTile, clearFocus };
initNightboard();
initJointPopover();
initTray();

// Review Dock — dev-only in-page human→AI feedback bar. No-ops unless BOTH
// local mode AND localStorage.ac_review_dock==='1' (the server's
// AUTOCUE_REVIEW_DOCK env-gate is the second, independent guard). Last so the
// workbench/places exist when _derivePage() reads the current page.
import { initReviewDock } from './review-dock.js';
initReviewDock();
