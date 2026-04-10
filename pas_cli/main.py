"""PAS CLI entrypoint: ``pas project init`` and future subcommands."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="PAS toolkit — project config and helpers.")
project_app = typer.Typer(help="Project (.pas.yaml) commands.")
app.add_typer(project_app, name="project")


def _prompt_yes_no(message: str, *, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        line = input(message + suffix).strip().lower()
    except EOFError:
        return not default_no
    if not line:
        return not default_no
    return line in ("y", "yes")


def _write_ai_context_files(root: Path) -> None:
    from pas_core.ai_assistant_snippets import write_agents_md, write_cursor_rule

    p1 = write_agents_md(root)
    typer.echo(str(p1))
    p2 = write_cursor_rule(root)
    typer.echo(str(p2))


@project_app.command("init")
def project_init(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Project root directory (default: current working directory).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Non-interactive: skip prompts; use with --write-ai-context or --no-write-ai-context.",
    ),
    no_input: bool = typer.Option(
        False,
        "--no-input",
        help="Do not prompt (same conservative defaults as --yes).",
    ),
    write_ai_context: bool = typer.Option(
        False,
        "--write-ai-context",
        help="Update AGENTS.md (marked PAS section) and .cursor/rules/pas-context.mdc.",
    ),
    no_write_ai_context: bool = typer.Option(
        False,
        "--no-write-ai-context",
        help="Skip writing AI assistant files (non-interactive).",
    ),
) -> None:
    """Create .pas.yaml skeleton if missing; optionally add AI assistant context files."""
    from pas_core.config import init_pas_project_yaml

    if write_ai_context and no_write_ai_context:
        typer.echo("Cannot use both --write-ai-context and --no-write-ai-context.", err=True)
        raise typer.Exit(code=1)

    root = (path or Path.cwd()).expanduser().resolve()
    if not root.is_dir():
        typer.echo(f"Not a directory: {root}", err=True)
        raise typer.Exit(code=1)

    result = init_pas_project_yaml(root, ensure_gitignore=True)
    if result.skipped:
        typer.echo(f"PAS project file already exists: {result.path}")
    else:
        typer.echo(f"Created {result.path.name}")
        if result.gitignore_updated and result.gitignore_path:
            typer.echo(f"Updated {result.gitignore_path.name} for .pas.yaml")

    do_write: bool | None
    if write_ai_context:
        do_write = True
    elif no_write_ai_context:
        do_write = False
    elif yes or no_input:
        do_write = False
    else:
        do_write = None

    if do_write is None:
        do_write = _prompt_yes_no(
            "Update AI assistant files (AGENTS.md: append or refresh PAS section; "
            ".cursor/rules/pas-context.mdc: replace)?",
            default_no=True,
        )

    if do_write:
        try:
            _write_ai_context_files(root)
        except OSError as e:
            typer.echo(f"Write failed: {e}", err=True)
            raise typer.Exit(code=1) from e
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1) from e


def main() -> None:
    app()


if __name__ == "__main__":
    main()
