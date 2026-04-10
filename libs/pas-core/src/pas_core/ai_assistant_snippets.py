"""AI assistant onboarding snippets (AGENTS.md / Cursor .mdc).

Canonical summary text is mirrored in ``docs/ai-assistant-summary.md`` (pas-core package).
"""

from __future__ import annotations

import re
from pathlib import Path

PAS_AI_ASSISTANT_SUMMARY = (
    "If the project uses PAS (especially when `.pas.yaml` exists, even if gitignored), "
    "**environment and service-slot structure are owned by PAS Console / PAS toolkit**; "
    "assistants should **defer** to those tools and remind developers to update them "
    "instead of managing the same concerns ad hoc in the repo."
)

CURSOR_RULE_REL = (".cursor", "rules", "pas-context.mdc")
AGENTS_REL = ("AGENTS.md",)

# Wrapped block in AGENTS.md so we can replace in place without clobbering other sections.
AGENTS_MARKER_START = "<!-- pas-toolkit-ai-context:start -->"
AGENTS_MARKER_END = "<!-- pas-toolkit-ai-context:end -->"


def agents_md_snippet() -> str:
    return (
        "# Agent instructions (PAS)\n\n"
        "## Environment and `.pas.yaml`\n\n"
        f"{PAS_AI_ASSISTANT_SUMMARY}\n"
    )


def cursor_rule_mdc_snippet() -> str:
    return (
        "---\n"
        "description: PAS — env and service slots; prefer PAS Console or toolkit\n"
        "alwaysApply: true\n"
        "---\n\n"
        "# PAS / environment (coding assistants)\n\n"
        f"{PAS_AI_ASSISTANT_SUMMARY}\n"
    )


def validated_project_root(path_str: str) -> Path:
    root = Path(path_str).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Project root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    return root


def _write_under_root(root: Path, relative_parts: tuple[str, ...], content: str) -> Path:
    dest = root.joinpath(*relative_parts)
    dest = dest.resolve()
    try:
        dest.relative_to(root)
    except ValueError as e:
        raise ValueError(f"Refusing to write outside project root: {dest}") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8", newline="\n")
    return dest


def agents_md_marked_block() -> str:
    """PAS section plus HTML comment markers for idempotent upsert into AGENTS.md."""
    inner = agents_md_snippet().rstrip()
    return f"{AGENTS_MARKER_START}\n{inner}\n{AGENTS_MARKER_END}\n"


def write_agents_md(project_root: Path) -> Path:
    """Create or update ``AGENTS.md``: append a marked PAS block, or replace that block if present."""
    root = project_root.resolve()
    dest = root.joinpath(*AGENTS_REL)
    dest = dest.resolve()
    try:
        dest.relative_to(root)
    except ValueError as e:
        raise ValueError(f"Refusing to write outside project root: {dest}") from e

    block = agents_md_marked_block()
    pattern = re.compile(
        re.escape(AGENTS_MARKER_START) + r"\n[\s\S]*?\n" + re.escape(AGENTS_MARKER_END) + r"\n?",
        re.MULTILINE,
    )

    if not dest.is_file():
        dest.write_text(block, encoding="utf-8", newline="\n")
        return dest

    text = dest.read_text(encoding="utf-8")
    if pattern.search(text):
        new_text = pattern.sub(block, text, count=1)
    else:
        sep = "\n\n" if text.strip() else ""
        new_text = text.rstrip() + sep + block
    dest.write_text(new_text, encoding="utf-8", newline="\n")
    return dest


def write_cursor_rule(project_root: Path) -> Path:
    return _write_under_root(project_root, CURSOR_RULE_REL, cursor_rule_mdc_snippet())
