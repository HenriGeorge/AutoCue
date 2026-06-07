#!/bin/bash
# AutoCue doc-sync check.
# Verifies CLAUDE.md (and docs/FEATURES.md where applicable) reference every
# route, module, and test file currently in the codebase. Reports stale
# entries (mentioned in docs but no longer in code) and missing entries
# (in code but not mentioned in docs).
#
# Usage: bash .claude/hooks/check_doc_sync.sh [--brief]
#   --brief: one-line output, exit 2 if gaps > 0 (for use in hooks)
#
# Designed to be fast (<1s on AutoCue's size) and tolerant — it warns,
# it does not block commits unless invoked with --strict.

BRIEF_MODE=false
STRICT_MODE=false
for arg in "$@"; do
  [[ "$arg" == "--brief"  ]] && BRIEF_MODE=true
  [[ "$arg" == "--strict" ]] && STRICT_MODE=true
done

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
ROUTES_FILE="$PROJECT_DIR/autocue/serve/routes.py"
CLAUDE_MD="$PROJECT_DIR/CLAUDE.md"
FEATURES_MD="$PROJECT_DIR/docs/FEATURES.md"

if [[ ! -f "$CLAUDE_MD" ]]; then
  echo "WARNING: CLAUDE.md not found — skipping doc-sync check"
  exit 0
fi

# ---------------------------------------------------------------------
# 1. ROUTES: every @router.<method>("/path") should be in CLAUDE.md
# ---------------------------------------------------------------------
MISSING_ROUTES=0
STALE_ROUTES=0
MISSING_ROUTES_LIST=""
STALE_ROUTES_LIST=""

if [[ -f "$ROUTES_FILE" ]]; then
  # Extract every route path declared via @router.<method>("/path...") or
  # @router.<method>(\n    "/path...") — handles single-line + multi-line decorators.
  # Normalize param names: {track_id} → {id} so comparison against docs is loose.
  ROUTES=$(python3 -c "
import re, sys
text = open(sys.argv[1]).read()
seen = set()
for m in re.finditer(r'@router\.(get|post|put|delete|patch)\(\s*\"(/[^\"]*)\"', text):
    p = m.group(2)
    # Normalize param placeholders to {id} for docs-comparison
    p_norm = re.sub(r'\{[a-z_]+\}', '{id}', p)
    if p_norm and p_norm not in seen:
        seen.add(p_norm)
        print(p_norm)
" "$ROUTES_FILE")

  # CLAUDE.md routes are written with /api prefix; routes.py paths don't include it
  # (the router is mounted with prefix='/api' at include_router time).
  # So compare against /api{route}.
  while IFS= read -r route; do
    [[ -z "$route" ]] && continue
    full_route="/api${route}"
    # Build regex: escape slashes, allow any {param-name} for {id} segments
    pattern=$(echo "$full_route" | sed 's|/|\\/|g; s|{id}|{[a-z_]*}|g')
    if ! grep -qE "$pattern" "$CLAUDE_MD"; then
      MISSING_ROUTES=$((MISSING_ROUTES + 1))
      MISSING_ROUTES_LIST+="    $full_route"$'\n'
    fi
  done <<< "$ROUTES"

  # Reverse: every `/api/...` mentioned in CLAUDE.md should still exist in routes.py.
  # Strip /api prefix and normalize {x} placeholders, then check against the
  # extracted-and-normalized ROUTES list (so we don't re-parse routes.py here).
  DOC_ROUTES=$(grep -oE '/api/[a-zA-Z0-9_/{}-]+' "$CLAUDE_MD" | sort -u)
  while IFS= read -r doc_route; do
    [[ -z "$doc_route" ]] && continue
    clean_route=$(echo "$doc_route" | sed 's/[.,);:]*$//')
    # Strip /api prefix → matches what's in $ROUTES
    stripped=${clean_route#/api}
    # Normalize all {param-name} → {id} for loose comparison
    normalized=$(echo "$stripped" | sed 's|{[a-z_]*}|{id}|g')
    # Check if normalized path exists in extracted routes
    if ! echo "$ROUTES" | grep -qFx "$normalized"; then
      STALE_ROUTES=$((STALE_ROUTES + 1))
      STALE_ROUTES_LIST+="    $clean_route"$'\n'
    fi
  done <<< "$DOC_ROUTES"
fi

# ---------------------------------------------------------------------
# 2. MODULES: every autocue/**/*.py should be in CLAUDE.md architecture tree
# ---------------------------------------------------------------------
MISSING_MODULES=0
MISSING_MODULES_LIST=""

# Python files in autocue/, excluding __pycache__, __init__.py, tests
MODULES=$(find "$PROJECT_DIR/autocue" -name '*.py' -not -name '__init__.py' -not -name '__main__.py' 2>/dev/null | \
  sed "s|$PROJECT_DIR/||" | sort)

while IFS= read -r module; do
  [[ -z "$module" ]] && continue
  # Match by basename — CLAUDE.md uses filenames like `routes.py` in its tree
  basename=$(basename "$module")
  if ! grep -qF "$basename" "$CLAUDE_MD"; then
    MISSING_MODULES=$((MISSING_MODULES + 1))
    MISSING_MODULES_LIST+="    $module"$'\n'
  fi
done <<< "$MODULES"

# ---------------------------------------------------------------------
# 3. TEST FILES: every tests/test_*.py should be referenced
# ---------------------------------------------------------------------
MISSING_TESTS=0
MISSING_TESTS_LIST=""

TEST_FILES=$(find "$PROJECT_DIR/tests" -maxdepth 1 -name 'test_*.py' 2>/dev/null | \
  sed "s|$PROJECT_DIR/||" | sort)

while IFS= read -r test; do
  [[ -z "$test" ]] && continue
  basename=$(basename "$test")
  if ! grep -qF "$basename" "$CLAUDE_MD"; then
    MISSING_TESTS=$((MISSING_TESTS + 1))
    MISSING_TESTS_LIST+="    $test"$'\n'
  fi
done <<< "$TEST_FILES"

# ---------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------
TOTAL_GAPS=$((MISSING_ROUTES + STALE_ROUTES + MISSING_MODULES + MISSING_TESTS))

if [[ "$BRIEF_MODE" == true ]]; then
  if [[ "$TOTAL_GAPS" -gt 0 ]]; then
    echo "Doc sync: $TOTAL_GAPS gaps ($MISSING_ROUTES routes undoc'd, $STALE_ROUTES routes stale, $MISSING_MODULES modules undoc'd, $MISSING_TESTS tests undoc'd)" >&2
    exit 2
  fi
  echo "Doc sync: clean"
  exit 0
fi

echo "=== AutoCue Doc Sync Check ==="
echo ""
echo "Routes (in routes.py vs CLAUDE.md):"
echo "  Undocumented: $MISSING_ROUTES"
echo "  Stale entries (deleted from code): $STALE_ROUTES"
echo "Modules (in autocue/ vs CLAUDE.md):"
echo "  Undocumented: $MISSING_MODULES"
echo "Test files (in tests/ vs CLAUDE.md):"
echo "  Undocumented: $MISSING_TESTS"
echo ""

if [[ -n "$MISSING_ROUTES_LIST" ]]; then
  echo "Undocumented routes:"
  echo -n "$MISSING_ROUTES_LIST"
  echo ""
fi

if [[ -n "$STALE_ROUTES_LIST" ]]; then
  echo "Stale route entries in CLAUDE.md:"
  echo -n "$STALE_ROUTES_LIST"
  echo ""
fi

if [[ -n "$MISSING_MODULES_LIST" ]]; then
  echo "Undocumented modules:"
  echo -n "$MISSING_MODULES_LIST"
  echo ""
fi

if [[ -n "$MISSING_TESTS_LIST" ]]; then
  echo "Undocumented test files:"
  echo -n "$MISSING_TESTS_LIST"
  echo ""
fi

echo "Total gaps: $TOTAL_GAPS"
if [[ "$TOTAL_GAPS" == 0 ]]; then
  echo "All synced!"
fi

if [[ "$STRICT_MODE" == true && "$TOTAL_GAPS" -gt 0 ]]; then
  exit 2
fi
exit 0
