# Version Control Guide

## Branches

- `main`: stable, runnable, and demonstration-ready.
- `codex/<topic>`: Codex-assisted development, for example `codex/evidence-drawer`.
- `fix/<topic>`: manually maintained bug-fix work.
- `docs/<topic>`: documentation-only work.

Merge work into `main` only after the relevant tests pass. Never commit `.env` files, local databases, uploaded material, sessions, traces, or logs.

## Commits

Use concise Conventional Commit messages:

- `feat:` new capability
- `fix:` bug fix
- `refactor:` internal change without an external behavior change
- `test:` test coverage or test infrastructure
- `docs:` documentation
- `chore:` dependencies, tooling, or repository maintenance

Example: `feat: add evidence reference drawer`

## Versions

Versions use `MAJOR.MINOR.PATCH`:

- `MAJOR`: incompatible architecture or API change.
- `MINOR`: backward-compatible capability.
- `PATCH`: backward-compatible fix.

Before each release, update `CHANGELOG.md` and create an annotated tag on `main`:

```powershell
git tag -a v0.5.1 -m "Interview Review Assistant v0.5.1"
git push origin main --follow-tags
```

Published tags are immutable. Do not move or replace an existing release tag.

## Release Checklist

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check frontend\app.js
node --check frontend\data-model.js
git diff --check
git status --short
```

Confirm that tests pass, the worktree contains only intended changes, and the staged diff contains no credentials or private interview material before pushing.
