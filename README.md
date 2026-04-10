# PAS toolkit (submodule)

This directory is the **PAS toolkit** checkout: **`pas-core`** (`libs/pas-core/`) and the **`pas`** CLI (`pas_cli/`).

## CLI

From this folder:

```bash
uv sync
uv run pas --help
uv run pas project init --path /path/to/repo
```

`pas project init` creates `.pas.yaml` when missing and can prompt (or use `--write-ai-context` / `--no-write-ai-context` with `--yes`) to update `AGENTS.md` (marked PAS section appended or refreshed) and `.cursor/rules/pas-context.mdc` (replaced).

## Packages

- **`libs/pas-core`** — installable `pas-core` (shared with pas-console via path dependency).
- **`pas-toolkit`** (this `pyproject.toml`) — Typer CLI, depends on `pas-core`.
