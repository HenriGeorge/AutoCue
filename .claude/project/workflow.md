# Development Workflow

## Git & Commits

- Conventional commits: `<type>(<scope>): <summary>`
- Branch per feature — never commit directly to main
- Always `git add <specific-files>` (no `git add .` or `-A`)
- Rebase on main before creating a PR: `git fetch origin && git rebase origin/main`

## PRP Workflow (standard path for all features)

```
/prp-core:prp-prd   → define what to build (interactive)
/prp-core:prp-plan  → generate implementation plan
/prp-core:prp-ralph → autonomous execution loop until all validations pass
/prp-core:prp-pr    → open pull request
```

For a quick all-in-one: `/prp-core-runner <feature description>`

## PRD-First Philosophy

Write the PRD before implementation. The richer the spec, the more autonomous Ralph can run without asking questions. PRDs live in the root `PRPs/` directory (e.g. `PRPs/001-schema-and-discogs-import.md`).

Naming format:
```
NNN-descriptive-name.md
```

## Branching

| Prefix | Use |
|---|---|
| `feature/*` | New functionality |
| `fix/*` | Bug fixes |
| `chore/*` | Config, deps, tooling |
| `docs/*` | Documentation only |
