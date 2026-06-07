Turn open GitHub issues filed by `/autocue-qa` (or any `bug`-labelled issue) into PRs.

Before invoking the Workflow:

1. **Reap stale marker.** If `.claude/state/fixer-running` exists and its mtime is > 6h old, delete it (orphaned from a crashed run).
2. **Create fresh marker.** `mkdir -p .claude/state && touch .claude/state/fixer-running`. The marker tells `.claude/hooks/stop_log.py` to short-circuit its "unpushed work" reminder so the fixer's autonomy isn't broken between Stop events.
3. **Invoke the Workflow** (passes the args through):
   - `/autocue-fixer <num>` → `Workflow({ scriptPath: ".claude/workflows/autocue-fixer.ts", args: { issues: [num], dryRun: false } })`
   - `/autocue-fixer <num> <num> ...` → `Workflow({ ..., args: { issues: [...], dryRun: false } })`
   - `/autocue-fixer` (no arg) → `Workflow({ ..., args: { issues: [], dryRun: false } })`. Workflow fetches every open `bug`-labelled issue itself.
   - `/autocue-fixer --dry-run [...]` → same shape but `dryRun: true`. Workflow performs Phase 0 + 1 only; no `gh` mutations, no `git push`.
4. **Trap.** Always `rm -f .claude/state/fixer-running` after the Workflow returns — on success OR failure.

Safety contract (enforced by the agent + the workflow's post-fix safety scan):

- Sandbox-only writes — never touches the real `master.db` or anything under `~/Library/Pioneer/`.
- Never bypasses `db_writer.rekordbox_is_running()`.
- Never commits `.env`, credentials, secrets, or `*.db` files.
- Never widens the CORS whitelist in `autocue/serve/app.py`.
- ≤ 50-line diff preferred per fix.

Full documentation with mermaid diagrams: `docs/qa_fixer.md`. Agent prompt: `.claude/agents/autocue-fixer.md`. Workflow script: `.claude/workflows/autocue-fixer.ts`. Config: `.claude/fixer.yaml`.
