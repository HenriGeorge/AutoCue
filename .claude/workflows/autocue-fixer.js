export const meta = {
  name: "autocue-fixer",
  description:
    "Turn open GitHub issues into PRs. Dedups, groups by file overlap, spawns one isolated worktree-per-issue agent, then runs a deterministic safety scan over each PR diff.",
  whenToUse:
    "After /autocue-qa has filed issues, run /autocue-fixer to convert open bug issues into PRs.",
  phases: [
    { title: "Fetch", detail: "list open issues + dedup" },
    { title: "Group", detail: "file-overlap dependency analysis" },
    { title: "Fix", detail: "one agent per issue, isolated worktree" },
    { title: "MergePlan", detail: "detect PR overlaps, print merge order" },
    { title: "SafetyScan", detail: "grep each PR diff for forbidden patterns" },
  ],
};

// ───────────────────────────────────────────────────────────────────────────
// Helpers (no FS / network — pure orchestration)
// ───────────────────────────────────────────────────────────────────────────

// Run a shell command via an agent and parse its output. Workflow scripts
// can't call Bash directly — they orchestrate agents. We use a thin "shell"
// agent for read-only `gh` / `git` queries.
const SHELL_SCHEMA = {
  type: "object",
  properties: {
    stdout: { type: "string" },
    exitCode: { type: "number" },
  },
  required: ["stdout", "exitCode"],
  additionalProperties: false,
};

async function sh(cmd, label) {
  const r = await agent(
    `Execute this shell command in the current working directory using the Bash tool:\n\n\`\`\`\n${cmd}\n\`\`\`\n\nReturn the result via the StructuredOutput tool with two fields:\n- stdout: the LITERAL stdout bytes the command produced. An empty string is valid. Do NOT JSON-encode it. Do NOT wrap it in an object.\n- exitCode: the integer exit code.\n\nExample of CORRECT output for a command that printed "42" and exited 0:\n  {"stdout": "42", "exitCode": 0}\n\nExample of INCORRECT output (do not do this):\n  {"stdout": "{\\"stdout\\": \\"42\\", \\"exitCode\\": 0}", "exitCode": 0}\n\nDo not paraphrase, summarize, or add commentary to stdout. Return the raw bytes only.`,
    { label, schema: SHELL_SCHEMA, phase: "Fetch" },
  );
  // Defensive unwrap: if the agent put the envelope inside .stdout, peel it.
  if (r && typeof r.stdout === "string" && /^\s*\{\s*"stdout"\s*:/.test(r.stdout)) {
    try {
      const inner = JSON.parse(r.stdout);
      if (inner && typeof inner === "object" && typeof inner.stdout === "string") {
        return { stdout: inner.stdout, exitCode: typeof inner.exitCode === "number" ? inner.exitCode : r.exitCode };
      }
    } catch {}
  }
  return r;
}

function dedupeAndValidateIssues(input) {
  if (!Array.isArray(input)) return [];
  const seen = new Set();
  const out = [];
  for (const v of input) {
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1 || n > 99999) continue;
    if (seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }
  return out;
}

// Extract file paths from issue body text. Used for dependency grouping.
// Regex covers common AutoCue file types (per plan v6).
const FILE_RX =
  /[a-zA-Z0-9_/.-]+\.(py|ts|js|tsx|jsx|html|css|md|yaml|yml|toml|json)\b/g;

function extractFiles(body) {
  const out = new Set();
  let m;
  while ((m = FILE_RX.exec(body)) !== null) out.add(m[0]);
  // docs/index.html is excluded from the overlap key — every UI bug names
  // it, and grouping on it would serialize every UI fix. True conflicts
  // get caught at merge-planning instead.
  out.delete("docs/index.html");
  return [...out];
}

// Group issues by file overlap. Issues sharing any file go in the same
// group (sequential within); groups run in parallel.
function groupByOverlap(items) {
  const groups = [];
  const fileMap = new Map(
    items.map((it) => [it.num, new Set(it.files)]),
  );
  const assigned = new Set();

  for (const item of items) {
    if (assigned.has(item.num)) continue;
    const group = [item.num];
    assigned.add(item.num);
    for (const other of items) {
      if (assigned.has(other.num)) continue;
      const a = fileMap.get(item.num);
      const b = fileMap.get(other.num);
      const overlap = [...a].some((f) => b.has(f));
      if (overlap) {
        group.push(other.num);
        assigned.add(other.num);
      }
    }
    groups.push(group);
  }
  return groups;
}

// ───────────────────────────────────────────────────────────────────────────
// Workflow entry
// ───────────────────────────────────────────────────────────────────────────

const inputIssues = dedupeAndValidateIssues((args && args.issues) || []);
const dryRun = !!(args && args.dryRun);

phase("Fetch");

// ── Step 1: Fetch ─────────────────────────────────────────────────────────
let candidates;
if (inputIssues.length === 0) {
  log("No explicit issue list — fetching all open `bug`-labelled issues.");
  const res = await sh(
    `gh issue list --label bug --state open --json number --jq '.[].number'`,
    "fetch:open-bugs",
  );
  candidates = res.stdout
    .split("\n")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
} else {
  candidates = inputIssues;
}

log(`Candidates: ${candidates.length} (${candidates.join(", ")})`);

// ── Step 2: Dedup ─────────────────────────────────────────────────────────
const deduped = [];
for (const num of candidates) {
  const state = await sh(`gh issue view ${num} --json state --jq '.state'`, `dedup:state-${num}`);
  if (state.stdout.trim() === "CLOSED") {
    log(`  [dedup] #${num} — issue closed, skip.`);
    continue;
  }

  // gh honours one --head per invocation — two calls.
  const exact = await sh(
    `gh pr list --state all --head "fix/${num}" --json number --jq '.[0].number // empty'`,
    `dedup:pr-exact-${num}`,
  );
  if (exact.stdout.trim()) {
    log(`  [dedup] #${num} — PR #${exact.stdout.trim()} (exact head) already exists, skip.`);
    continue;
  }
  const prefix = await sh(
    `gh pr list --state all --head "fix/${num}-" --json number --jq '.[0].number // empty'`,
    `dedup:pr-prefix-${num}`,
  );
  if (prefix.stdout.trim()) {
    log(`  [dedup] #${num} — PR #${prefix.stdout.trim()} (prefix head) already exists, skip.`);
    continue;
  }

  const closedOnMain = await sh(
    `git log main --grep="Closes #${num}" -1 --oneline`,
    `dedup:closed-${num}`,
  );
  if (closedOnMain.stdout.trim()) {
    log(`  [dedup] #${num} — already on main: ${closedOnMain.stdout.trim()}. Closing.`);
    if (!dryRun) {
      await sh(
        `gh issue close ${num} --comment "Fixed on main: ${closedOnMain.stdout.trim()}"`,
        `dedup:close-${num}`,
      );
    }
    continue;
  }

  deduped.push(num);
}

if (deduped.length === 0) {
  log("Nothing to do after dedup. Done.");
  return { fixed: [], skipped: candidates };
}

// ── Step 3: Group by file overlap ─────────────────────────────────────────
phase("Group");

const withFiles = await Promise.all(
  deduped.map(async (num) => {
    const body = await sh(`gh issue view ${num} --json body --jq '.body'`, `group:body-${num}`);
    return { num, files: extractFiles(body.stdout || "") };
  }),
);

const groups = groupByOverlap(withFiles);
log(`Groups: ${groups.length}`);
for (const g of groups) {
  log(`  ${g.length === 1 ? "parallel" : "sequential"}: ${g.join(", ")}`);
}

if (dryRun) {
  log("DRY RUN — stopping before agent fan-out.");
  return { wouldFix: deduped, groups, dryRun: true };
}

// ── Step 4: Fan-out (one agent per issue) ─────────────────────────────────
phase("Fix");

// Sequential within group, parallel across groups.
const allFixed = [];
const groupResults = await parallel(
  groups.map((group) => async () => {
    const out = [];
    for (const num of group) {
      const result = await agent(
        `You are the autocue-fixer agent. Read .claude/agents/autocue-fixer.md and run the full Phase 0 → 4 flow for GitHub issue #${num}. dry_run=false.`,
        {
          label: `fix-${num}`,
          phase: "Fix",
          isolation: "worktree",
          agentType: "general-purpose",
        },
      );
      out.push({ num, result });
    }
    return out;
  }),
);
for (const g of groupResults) {
  if (g) allFixed.push(...g);
}

// ── Step 5: Merge planning ────────────────────────────────────────────────
phase("MergePlan");

const prFiles = new Map();
for (const { num } of allFixed) {
  // `gh pr list --head <ref>` only matches the exact ref, but the fix-agent
  // names branches `fix/<num>-<slug>`. List open PRs and JQ-filter on the
  // prefix `fix/<num>` or `fix/<num>-` against `headRefName`.
  const prNum = await sh(
    `gh pr list --state open --limit 200 --json number,headRefName --jq '[.[] | select(.headRefName == "fix/${num}" or (.headRefName | startswith("fix/${num}-")))] | .[0].number // empty'`,
    `mergeplan:pr-num-${num}`,
  );
  const pr = prNum.stdout.trim();
  if (!pr) {
    log(`  No open PR found for #${num} (agent may have stopped before push).`);
    continue;
  }
  const stat = await sh(
    `gh pr diff ${pr} --name-only`,
    `mergeplan:diff-${pr}`,
  );
  prFiles.set(Number(pr), stat.stdout.split("\n").map((s) => s.trim()).filter(Boolean));
}

const prNums = [...prFiles.keys()];
for (let i = 0; i < prNums.length; i++) {
  for (let j = i + 1; j < prNums.length; j++) {
    const a = prFiles.get(prNums[i]);
    const b = prFiles.get(prNums[j]);
    const overlap = a.filter((f) => b.includes(f));
    if (overlap.length) {
      const msg = `PR #${prNums[i]} and PR #${prNums[j]} both touch: ${overlap.join(", ")}. Merge whichever is smaller first, then \`/autocue-fixer <other>\` to re-run Phase 2 on top of fresh main.`;
      log(`COORDINATION: ${msg}`);
      await sh(
        `printf '%s\\n' '${msg.replace(/'/g, "'\\''")}' >> .claude/reports/dispatch-coordination.log`,
        `mergeplan:log-overlap`,
      );
    }
  }
}

// ── Step 6: Post-fix safety scan ──────────────────────────────────────────
phase("SafetyScan");

const FORBIDDEN_PATTERN =
  String.raw`\.env|credentials|password|api[_-]?key|secret|~/Library/Pioneer/|master\.db`;

for (const pr of prNums) {
  const grepRes = await sh(
    `gh pr diff ${pr} | grep -nEi '${FORBIDDEN_PATTERN}' || true`,
    `safety:grep-${pr}`,
  );
  if (grepRes.stdout.trim()) {
    log(`SAFETY BLOCKED: PR #${pr} contains forbidden patterns:\n${grepRes.stdout}`);
    await sh(
      `gh pr edit ${pr} --add-label "safety:blocked"`,
      `safety:label-${pr}`,
    );
    await sh(
      `gh pr comment ${pr} --body "Post-fix safety scan flagged forbidden patterns. Review before merge.\\n\\n\\\`\\\`\\\`\\n${grepRes.stdout.replace(/`/g, "\\`").slice(0, 1800)}\\n\\\`\\\`\\\`"`,
      `safety:comment-${pr}`,
    );
    await sh(
      `printf 'PR #%s: safety scan failed\\n%s\\n' '${pr}' '${grepRes.stdout.replace(/'/g, "'\\''").slice(0, 1800)}' >> .claude/reports/dispatch-coordination.log`,
      `safety:log-${pr}`,
    );
  }
}

return { fixed: [...prFiles.keys()], skipped: candidates.filter((n) => !deduped.includes(n)) };
