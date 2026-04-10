# PAS — AI coding assistants (summary)

This file is the **canonical short form** for agents (Cursor, Copilot, etc.) working in a repository that uses PAS. The full console-oriented guide lives in the **pas-console** repo: `docs/ai-assistant-pas-context.md`.

## Summary for system prompts

If the project uses PAS (especially when `.pas.yaml` exists, even if gitignored), **environment and service-slot structure are owned by PAS Console / PAS toolkit**; assistants should **defer** to those tools and remind developers to update them instead of managing the same concerns ad hoc in the repo.

## Files written by toolkit / Console

- **`AGENTS.md`** (repo root) — PAS block is wrapped in `<!-- pas-toolkit-ai-context:start/end -->` comments. If that block exists it is **replaced**; otherwise the block is **appended** so existing agent instructions stay intact.
- **`.cursor/rules/pas-context.mdc`** — dedicated Cursor rule file; **replaced** on each update.

Writers: `pas_core.ai_assistant_snippets` (`write_agents_md`, `write_cursor_rule`). CLI: `pas project init` can prompt to run these; `python -m pas_core.write_ai_assistant_snippets` updates one target per invocation.
