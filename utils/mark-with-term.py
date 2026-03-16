#!/usr/bin/env python3
"""
@pas-executable
Append a marker term to file and folder names when missing.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Tuple

from rich.panel import Panel

# Add project root to sys.path so we can find 'helpers'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core import console  # type: ignore[import]

# --- Tool Identity & Defaults ---
TOOL_ID = "mark-with-term"
TOOL_TITLE = "Marker Term Appender"
TOOL_SHORT_DESC = "Append a marker term to file and folder names if missing."
TOOL_DESCRIPTION = (
    "Scan a directory tree and append a marker term (e.g. 'bug') to file and/or "
    "folder names that do not already contain that term. Useful for normalizing "
    "names before cleanup tools that preserve only marked entries."
)

DEFAULT_MODE = "dirs"  # dirs | files | both
DEFAULT_DRY_RUN = True
# -------------------------------


def has_term_segment(name: str, term: str) -> bool:
    """
    Return True if `term` appears as its own segment within `name`.

    Segments are split on non-alphanumeric/underscore characters, so this
    correctly handles names like:
      - 'abc-bug-xyz'
      - 'abc bug xyz'
      - 'abc_bug_xyz'

    and avoids matching inside larger tokens like 'debug'.

    Matching is case-insensitive: 'BUG', 'Bug', and 'bug' are treated the same.
    """
    if not term:
        return False

    term_lower = term.lower()
    segments = [s.lower() for s in re.split(r"[^\w]+", name) if s]
    return term_lower in segments


def show_summary() -> None:
    """Display a brief summary of the tool's capabilities."""
    summary = (
        f"[bold cyan]{TOOL_ID}[/bold cyan]: {TOOL_DESCRIPTION}\n\n"
        "[bold]Typical workflow:[/bold]\n"
        "- Run in dry-run mode to see planned renames.\n"
        "- Review the old → new paths.\n"
        "- Re-run with --apply to perform the renames.\n\n"
        "[bold]Examples:[/bold]\n"
        "  mark-with-term --root /path/to/folders --term bug --mode dirs\n"
        "  mark-with-term --root /path/to/tree --term keep --mode both --apply"
    )
    console.print(Panel(summary, title=TOOL_TITLE, expand=False))


def iter_targets(root: Path, mode: str) -> Iterable[Path]:
    """
    Yield paths under root according to the selected mode.

    We deliberately skip the root itself to avoid renaming the base directory.
    """
    if mode not in {"dirs", "files", "both"}:
        raise ValueError(f"Invalid mode: {mode}")

    for path in sorted(root.rglob("*")):
        if mode == "dirs" and not path.is_dir():
            continue
        if mode == "files" and not path.is_file():
            continue
        if mode == "both" and not (path.is_file() or path.is_dir()):
            continue
        yield path


def plan_rename(path: Path, term: str) -> Tuple[Path, Path] | None:
    """
    Return (old, new) path if a rename is needed, otherwise None.

    - If the basename already contains the term as a standalone segment, no change.
    - Otherwise append '-{term}' to the basename.
    """
    name = path.name
    if has_term_segment(name, term):
        return None

    new_name = f"{name}-{term}"
    new_path = path.with_name(new_name)
    return path, new_path


def apply_renames(
    plans: Iterable[Tuple[Path, Path]],
    *,
    dry_run: bool,
    root: Path,
) -> None:
    """Execute or display renames based on dry_run.

    When operating in the current working directory (root == cwd), display
    relative paths instead of full absolute paths to keep output compact.
    """
    cwd = Path.cwd().resolve()
    root_resolved = root.resolve()

    any_changes = False
    for old, new in plans:
        # Prefer relative display when root is the current directory
        if root_resolved == cwd:
            try:
                old_disp = old.relative_to(root_resolved)
                new_disp = new.relative_to(root_resolved)
            except ValueError:
                old_disp = old
                new_disp = new
        else:
            old_disp = old
            new_disp = new

        any_changes = True
        if dry_run:
            console.print(f"[dim]{old_disp}[/dim] -> [bold]{new_disp}[/bold]")
            continue

        if new.exists():
            console.print(
                f"[yellow]Skipping[/yellow] {old_disp} -> {new_disp} "
                "(target already exists)."
            )
            continue

        old.rename(new)
        console.print(f"[green]Renamed[/green] {old_disp} -> {new_disp}")

    if not any_changes:
        console.print("[cyan]No entries required renaming.[/cyan]")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Root directory to scan (default: current working directory).",
    )
    parser.add_argument(
        "--term",
        required=True,
        help='Marker term to enforce in names (e.g. "bug").',
    )
    parser.add_argument(
        "--mode",
        choices=["dirs", "files", "both"],
        default=DEFAULT_MODE,
        help="What to rename: dirs, files, or both (default: dirs).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform renames (default: dry-run only).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    root: Path = args.root.expanduser().resolve()
    term: str = args.term
    mode: str = args.mode
    dry_run: bool = not args.apply if DEFAULT_DRY_RUN else args.apply

    show_summary()

    if not root.exists() or not root.is_dir():
        console.print(f"[red]Root directory does not exist or is not a directory:[/red] {root}")
        raise SystemExit(1)

    console.print(
        f"\n[bold]Scanning[/bold] {root} "
        f"(mode={mode}, term='{term}', dry_run={dry_run})\n"
    )

    plans = []
    for p in iter_targets(root, mode):
        planned = plan_rename(p, term)
        if planned is not None:
            plans.append(planned)

    apply_renames(plans, dry_run=dry_run, root=root)


if __name__ == "__main__":
    main()

