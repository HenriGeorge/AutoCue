import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Typed view onto control-inventory.json. The JSON is the source of truth;
 * this file just gives us TypeScript narrowing for the rest of the suite.
 *
 * NEVER hand-write the inventory rows here. Edit the JSON instead.
 */

export type ControlKind =
  | "button"
  | "checkbox"
  | "select"
  | "number"
  | "search"
  | "text"
  | "password"
  | "range";

export type ControlRow = {
  id: string;
  kind: ControlKind;
  /**
   * Section ids whose inline `display: none` must be cleared (in order) before
   * this control becomes reachable. Used by the drift guard's enumeration step.
   */
  collapsible?: string[];
  /**
   * Defense-in-depth flag. Today every sweep runs against a sandbox-bound
   * server (Playwright config copies master.db); `safeOnRealDb: false` rows
   * are skipped only if a future contributor ever points the harness at a
   * real DB.
   */
  safeOnRealDb?: boolean;
  /**
   * Reasons a row is skipped entirely. Currently used for the Discogs token
   * password field — we must never type into it or log its value.
   */
  skipSweep?: boolean;
  skipReason?: string;
  /**
   * Click strategy override for the per-control sweep.
   * - "force" (issue #190): skip Playwright's scrollIntoView precheck. Needed
   *   for `position: fixed` buttons (e.g. action-bar-*) — Playwright sees
   *   them as "outside viewport" and burns 30 s retrying scroll on an element
   *   that ignores scroll by definition.
   * Default (undefined) = normal click with viewport precheck.
   */
  clickStrategy?: "force";
  /**
   * State the sweep must establish BEFORE interacting with this row.
   * - "select-track": tick the first track card's bulk-select checkbox so a
   *   selection exists. Needed for the #action-bar rows — the bar is
   *   `aria-hidden` + translated off-viewport until `selectedTrackIds` is
   *   non-empty (`updateSelectionBar` in docs/index.html), so clicking its
   *   buttons without a selection can never succeed (and force-clicking, the
   *   old workaround, fails with "Element is outside of the viewport").
   */
  setup?: "select-track";
  /**
   * Console-error substrings that are EXPECTED when exercising this row in
   * the sandbox harness, and must not fail the sweep's clean-console
   * assertion. Use sparingly and document why on the row. Example:
   * disc-v2-refresh-btn — /api/discover/feed 400s ("DISCOGS_TOKEN not
   * configured") because the sandbox server has no token; the UI handles
   * the 400 as a structured scanError, but the browser still logs a
   * "Failed to load resource" console error for the response itself.
   */
  allowedConsoleErrors?: string[];
};

export type PerTrackRow = {
  selector: string;
  name: string;
  sampleCount: number;
};

export type Inventory = {
  $schema?: string;
  version: number;
  notes: string[];
  globalControls: ControlRow[];
  panelControls: {
    cues: ControlRow[];
    library: ControlRow[];
    discover: ControlRow[];
  };
  perTrack: PerTrackRow[];
};

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const raw = fs.readFileSync(
  path.join(__dirname, "control-inventory.json"),
  "utf-8",
);

export const INVENTORY: Inventory = JSON.parse(raw);

/** All known panel slugs the slash command's --scope arg accepts. */
export const PANEL_NAMES = ["cues", "library", "discover"] as const;
export type PanelName = (typeof PANEL_NAMES)[number];

/** Every id in the inventory (for drift guard's set-diff). */
export function allInventoryIds(): Set<string> {
  const set = new Set<string>();
  for (const r of INVENTORY.globalControls) set.add(r.id);
  for (const name of PANEL_NAMES) {
    for (const r of INVENTORY.panelControls[name]) set.add(r.id);
  }
  return set;
}

/** Rows scoped to a panel (or every row when scope is undefined). */
export function rowsForScope(scope: PanelName[] | undefined): ControlRow[] {
  const panels: PanelName[] =
    scope && scope.length > 0 ? scope : [...PANEL_NAMES];
  const out: ControlRow[] = [...INVENTORY.globalControls];
  for (const name of panels) out.push(...INVENTORY.panelControls[name]);
  return out;
}
