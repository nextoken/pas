"""CLI entry: write AGENTS.md or Cursor rule (``python -m pas_core.write_ai_assistant_snippets``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pas_core.ai_assistant_snippets import (
    validated_project_root,
    write_agents_md,
    write_cursor_rule,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--project-root",
        required=True,
        help="Absolute or user-relative path to the repository root.",
    )
    p.add_argument(
        "--mode",
        choices=("agents", "cursor"),
        required=True,
        help="Write AGENTS.md or .cursor/rules/pas-context.mdc",
    )
    args = p.parse_args(argv)
    try:
        root = validated_project_root(args.project_root)
        if args.mode == "agents":
            path = write_agents_md(root)
        else:
            path = write_cursor_rule(root)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Write failed: {e}", file=sys.stderr)
        return 1
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
