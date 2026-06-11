#!/bin/bash
# Block 'git add .' and 'git add -A' — force explicit file staging
# Prevents one Claude terminal from accidentally staging another terminal's work
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Extract just the first command before any && or ; chains
FIRST_CMD=$(echo "$COMMAND" | sed 's/ &&.*//' | sed 's/ ;.*//')

# Check for dangerous git add patterns in the first command only:
# - 'git add .' (stage everything in cwd)
# - 'git add -A' or 'git add --all' (stage everything)
if echo "$FIRST_CMD" | grep -qE '^git add (-A|--all)$'; then
  echo "BLOCKED: Use 'git add <specific-files>' instead of 'git add -A'" >&2
  exit 2
fi
if echo "$FIRST_CMD" | grep -qE '^git add \.$'; then
  echo "BLOCKED: Use 'git add <specific-files>' instead of 'git add .'" >&2
  exit 2
fi

exit 0
