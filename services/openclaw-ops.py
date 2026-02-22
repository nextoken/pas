#!/usr/bin/env python3
"""
@pas-executable
Check OpenClaw environment (Homebrew, pyenv, Python 11, nvm, Node LTS) and show account isolation warnings.

Setup is strongly opinionated: per-user Homebrew (~/.local), Python 3.11 (PAS-aligned), nvm + Node LTS,
isolated macOS account (default openclaw). Use a different workflow if you need other conventions.
"""

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    format_menu_choices,
    prompt_toolkit_menu,
    prompt_yes_no,
    run_command,
)
from rich.panel import Panel
from rich.table import Table

# --- Configuration ---
OPENCLAW_DOCS_URL = "https://github.com/openclaw/openclaw"
HOMEBREW_INSTALL_URL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
NVM_INSTALL_URL = "https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh"
# User Homebrew prefix (per-user install, no sudo)
USER_HOMEBREW_PREFIX = Path.home() / ".local"
# System Homebrew prefixes
SYSTEM_BREW_PREFIXES = ("/opt/homebrew", "/usr/local")
# Required Python minor for PAS alignment
REQUIRED_PYTHON_MINOR = (3, 11)
# Isolated account for OpenClaw (macOS user; visible, not headless)
DEFAULT_OPENCLAW_USER = "openclaw"
# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "openclaw-ops"
TOOL_TITLE = "OpenClaw environment checker"
TOOL_SHORT_DESC = "Check OpenClaw environment (Homebrew, pyenv, Python 11, nvm, Node LTS) and account isolation."
TOOL_DESCRIPTION = (
    "Checks your environment for OpenClaw: personalized Homebrew, pyenv, Python 3.11 (PAS-aligned), "
    "nvm, and Node LTS. Reminds you to use an isolated email and GitHub account—not your primary identity. "
    "Setup is strongly opinionated; use another workflow if you need different conventions."
)
# ---------------------


def _ensure_brew_in_path() -> None:
    """Prepend user Homebrew bin to PATH so child processes see brew."""
    brew_bin = USER_HOMEBREW_PREFIX / "bin"
    if (brew_bin / "brew").exists():
        existing = os.environ.get("PATH", "")
        if str(brew_bin) not in existing.split(os.pathsep):
            os.environ["PATH"] = str(brew_bin) + os.pathsep + existing


def _run(
    cmd: Optional[list] = None,
    capture: bool = True,
    shell_cmd: Optional[str] = None,
) -> Tuple[int, str, str]:
    """Run command; if shell_cmd is set, run via bash -lc. Returns (returncode, stdout, stderr)."""
    if shell_cmd is not None:
        cmd = ["/bin/bash", "-lc", shell_cmd]
    if cmd is None:
        return (1, "", "no command")
    res = run_command(cmd, capture_output=capture)
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    return (res.returncode, out, err)


def check_homebrew_personalized() -> Tuple[bool, str]:
    """Check that Homebrew is available and preferably user-installed (e.g. ~/.local)."""
    # Prefer user prefix first
    brew_user = USER_HOMEBREW_PREFIX / "bin" / "brew"
    if brew_user.exists() and os.access(brew_user, os.X_OK):
        return True, f"User Homebrew at {USER_HOMEBREW_PREFIX}"
    # Then system brew
    for prefix in SYSTEM_BREW_PREFIXES:
        brew_path = Path(prefix) / "bin" / "brew"
        if brew_path.exists() and os.access(brew_path, os.X_OK):
            return True, f"System Homebrew at {prefix} (consider user install to ~/.local for isolation)"
    # Try PATH
    rc, out, _ = _run(["brew", "--version"])
    if rc == 0:
        rc2, prefix_out, _ = _run(["brew", "--prefix"])
        if rc2 == 0 and prefix_out:
            p = Path(prefix_out).resolve()
            if str(p).startswith(str(Path.home())):
                return True, f"Homebrew at {p} (user)"
            return True, f"Homebrew at {prefix_out} (system)"
    return False, "Homebrew not found. Install to ~/.local for per-user use (see PAS installer)."


def check_pyenv() -> Tuple[bool, str]:
    """Check if pyenv is available."""
    rc, out, _ = _run(["pyenv", "--version"])
    if rc == 0:
        return True, out or "pyenv available"
    return False, "pyenv not found. Install via: brew install pyenv (and configure shell)."


def check_python_11() -> Tuple[bool, str]:
    """Check that Python 3.11.x is available (current interpreter or pyenv)."""
    try:
        v = sys.version_info
        if (v.major, v.minor) == REQUIRED_PYTHON_MINOR:
            return True, f"Python {v.major}.{v.minor}.{v.micro} (current)"
    except Exception:
        pass
    rc, out, _ = _run(["python3", "--version"])
    if rc == 0 and "3.11" in out:
        return True, out.strip()
    rc2, out2, _ = _run(["pyenv", "versions", "--bare"])
    if rc2 == 0:
        for line in out2.splitlines():
            line = line.strip()
            if line.startswith("3.11"):
                return True, f"Python 3.11 available via pyenv: {line}"
    return False, "Python 3.11 not found. Use pyenv: pyenv install 3.11.x (aligns with PAS)."


def check_nvm() -> Tuple[bool, str]:
    """Check if nvm (Node Version Manager) is available."""
    rc, out, _ = _run(shell_cmd="source \"${NVM_DIR:-$HOME/.nvm}/nvm.sh\" 2>/dev/null; nvm --version 2>/dev/null")
    if rc == 0 and out:
        return True, out.splitlines()[0] if out else "nvm available"
    rc2, out2, _ = _run(shell_cmd="[ -s \"${NVM_DIR:-$HOME/.nvm}/nvm.sh\" ] && echo ok")
    if rc2 == 0 and "ok" in (out2 or ""):
        return True, "nvm (NVM_DIR loaded)"
    return False, "nvm not found. Install from https://github.com/nvm-sh/nvm"


def check_nvm_lts() -> Tuple[bool, str]:
    """Check if Node LTS is installed/used via nvm."""
    rc, out, _ = _run(
        shell_cmd="source \"${NVM_DIR:-$HOME/.nvm}/nvm.sh\" 2>/dev/null; nvm list --no-colors 2>/dev/null | grep -E 'LTS|->' || true"
    )
    if rc != 0:
        return False, "Could not run nvm list (is nvm installed and sourced?)"
    if "LTS" in (out or "") or "->" in (out or ""):
        return True, "Node LTS available (use: nvm install --lts; nvm use --lts)"
    rc2, out2, _ = _run(shell_cmd="source \"${NVM_DIR:-$HOME/.nvm}/nvm.sh\" 2>/dev/null; nvm use --lts 2>&1; node -v 2>/dev/null")
    if out2:
        for line in out2.splitlines():
            line = line.strip()
            if line.startswith("v") and line[1:2].isdigit():
                return True, f"Node: {line}"
    return False, "Node LTS not installed. Run: nvm install --lts"


def show_account_warning() -> None:
    """Show warning: do not use primary account; use isolated email and GitHub."""
    console.print(Panel(
        "[bold red]Do NOT set up OpenClaw in your primary account.[/bold red]\n\n"
        "Use a separate identity to isolate automation and reduce risk:\n"
        "• [bold]Email[/bold]: Create an independent inbox (e.g. [bold]Gmail[/bold]) for OpenClaw and service accounts.\n"
        "• [bold]GitHub[/bold]: Create a dedicated GitHub account for automation and repo access.\n"
        "• Keep credentials and tokens for OpenClaw separate from your main personal accounts.",
        title="Account isolation",
        border_style="red",
    ))


def ask_setup_consent() -> bool:
    """Show account warning and ask user to confirm this is a secondary/isolated account. Returns True only if they confirm."""
    show_account_warning()
    return prompt_yes_no(
        "I confirm this is a secondary/isolated account (not my primary identity). Proceed with setting up personalized deps?",
        default=False,
    )


def _install_homebrew_user() -> bool:
    """Install Homebrew to ~/.local (per-user, no sudo). Returns True on success."""
    if check_homebrew_personalized()[0]:
        return True
    if not run_command(["git", "--version"], capture_output=True).returncode == 0:
        console.print("[red]Git is required to install Homebrew. Install Xcode Command Line Tools or Git first.[/red]")
        return False
    console.print("[cyan]Installing Homebrew to %s (per-user, no sudo)...[/cyan]" % USER_HOMEBREW_PREFIX)
    env = {**os.environ, "HOMEBREW_PREFIX": str(USER_HOMEBREW_PREFIX), "NONINTERACTIVE": "1"}
    # Official one-liner: bash runs the script from curl (env HOMEBREW_PREFIX/NONINTERACTIVE set above)
    r = subprocess.run(
        ["/bin/bash", "-c", "$(curl -fsSL " + HOMEBREW_INSTALL_URL + ")"],
        env=env,
        cwd=os.path.expanduser("~"),
    )
    if r.returncode != 0:
        console.print("[red]Homebrew install failed.[/red]")
        return False
    _ensure_brew_in_path()
    console.print("[green]Homebrew installed.[/green]")
    return True


def _install_pyenv() -> bool:
    """Install pyenv via Homebrew. Returns True on success."""
    if check_pyenv()[0]:
        return True
    _ensure_brew_in_path()
    console.print("[cyan]Installing pyenv via Homebrew...[/cyan]")
    r = run_command(["brew", "install", "pyenv"], capture_output=True)
    if r.returncode != 0:
        console.print("[red]pyenv install failed: %s[/red]" % (r.stderr or r.stdout or ""))
        return False
    console.print("[green]pyenv installed. Add pyenv init to your shell config (see: pyenv init).[/green]")
    return True


def _install_python_311() -> bool:
    """Install Python 3.11 via pyenv. Returns True on success."""
    if check_python_11()[0]:
        return True
    console.print("[cyan]Installing Python 3.11 via pyenv...[/cyan]")
    rc, out, err = _run(shell_cmd="eval \"$(pyenv init - 2>/dev/null)\" 2>/dev/null; pyenv install 3.11 --skip-existing 2>&1")
    if rc != 0:
        console.print("[red]Python 3.11 install failed: %s[/red]" % (err or out))
        return False
    console.print("[green]Python 3.11 installed.[/green]")
    return True


def _install_nvm() -> bool:
    """Install nvm to ~/.nvm. Returns True on success."""
    if check_nvm()[0]:
        return True
    console.print("[cyan]Installing nvm...[/cyan]")
    r = subprocess.run(
        ["/bin/bash", "-c", "curl -o- %s | bash" % NVM_INSTALL_URL],
        cwd=os.path.expanduser("~"),
        env=os.environ,
    )
    if r.returncode != 0:
        console.print("[red]nvm install failed.[/red]")
        return False
    console.print("[green]nvm installed. Source ~/.nvm/nvm.sh in your shell (or restart terminal).[/green]")
    return True


def _install_node_lts() -> bool:
    """Install Node LTS via nvm. Returns True on success."""
    if check_nvm_lts()[0]:
        return True
    console.print("[cyan]Installing Node LTS via nvm...[/cyan]")
    rc, out, err = _run(shell_cmd="source \"${NVM_DIR:-$HOME/.nvm}/nvm.sh\" 2>/dev/null; nvm install --lts 2>&1")
    if rc != 0:
        console.print("[red]Node LTS install failed: %s[/red]" % (err or out))
        return False
    console.print("[green]Node LTS installed.[/green]")
    return True


def setup_all_personalized() -> None:
    """Run consent gate then install any missing personalized deps (Homebrew, pyenv, Python 3.11, nvm, Node LTS)."""
    if not ask_setup_consent():
        console.print("[yellow]Setup cancelled. Use a secondary/isolated account before setting up OpenClaw deps.[/yellow]")
        return
    console.print("[bold]Setting up personalized deps...[/bold]")
    steps = [
        ("Homebrew (per-user)", _install_homebrew_user),
        ("pyenv", _install_pyenv),
        ("Python 3.11", _install_python_311),
        ("nvm", _install_nvm),
        ("Node LTS", _install_node_lts),
    ]
    for name, fn in steps:
        ok = fn()
        if not ok:
            console.print("[red]Stopping after %s failed.[/red]" % name)
            return
    console.print("[bold green]All personalized deps are set up. Restart your terminal or source your shell config to use them.[/bold green]")


def _user_ops_script() -> Path:
    """Path to user-ops script (compose via CLI)."""
    return Path(__file__).resolve().parent / "user-ops.py"


def _account_exists_local(username: str) -> bool:
    """Lightweight check if macOS user exists (dscl, no sudo). Used for panel display only."""
    if sys.platform != "darwin":
        return False
    r = run_command(["dscl", ".", "-read", f"/Users/{username}"], capture_output=True)
    return r.returncode == 0


def _user_exists_via_cli(username: str) -> bool:
    """Check if macOS user exists by calling user-ops --list (tool composability)."""
    if sys.platform != "darwin":
        return False
    script = _user_ops_script()
    if not script.exists():
        return False
    r = subprocess.run(
        [sys.executable, str(script), "--list"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    # Output lines like "openclaw (UID: 501, Hidden: True)"
    for line in (r.stdout or "").splitlines():
        if line.strip().startswith(username + " "):
            return True
    return False


def create_isolated_account(username: Optional[str] = None) -> None:
    """Create an isolated macOS user (visible, not headless) by invoking user-ops. Default: openclaw. Warns if exists."""
    if sys.platform != "darwin":
        console.print("[red]Isolated account creation is only supported on macOS.[/red]")
        return
    script = _user_ops_script()
    if not script.exists():
        console.print("[red]user-ops script not found at %s[/red]" % script)
        return
    name = (username or DEFAULT_OPENCLAW_USER).strip()
    if not name:
        console.print("[red]Username is required.[/red]")
        return
    try:
        current_user = getpass.getuser()
    except Exception:
        current_user = os.environ.get("USER", "")
    if current_user == name:
        try:
            current_uid = os.getuid()
        except Exception:
            current_uid = "—"
        console.print(Panel(
            f"[bold]Current account is already [bold white]'{name}'[/bold white].[/bold]\n\n"
            f"Current user: [bold]{current_user}[/bold] (UID: [bold]{current_uid}[/bold])\n\n"
            "You are in the isolated account; no need to create it.",
            title="Already in isolated account",
            border_style="blue",
        ))
        if not prompt_yes_no("Run create-account flow anyway (e.g. to run user-ops setup)?", default=False):
            return
    if _user_exists_via_cli(name):
        console.print(Panel(
            f"[bold yellow]Account [bold white]'{name}'[/bold white] already exists.[/bold yellow]\n\n"
            "Use this account for OpenClaw, or create a different user (e.g. via user-ops). Do not create a duplicate.",
            title="User exists",
            border_style="yellow",
        ))
        return
    show_account_warning()
    if not prompt_yes_no("Create isolated account %s on this Mac (visible, SSH-enabled)?" % name, default=False):
        console.print("[yellow]Cancelled.[/yellow]")
        return
    try:
        pwd = getpass.getpass("Password for new user %s: " % name)
        if not pwd:
            console.print("[red]Password cannot be empty.[/red]")
            return
        pwd2 = getpass.getpass("Confirm password: ")
        if pwd != pwd2:
            console.print("[red]Passwords do not match.[/red]")
            return
    except (EOFError, KeyboardInterrupt):
        console.print("[yellow]Cancelled.[/yellow]")
        return
    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--create", name,
            "--fullname", "OpenClaw isolated",
            "--no-hide",
            "--enable-ssh",
        ],
        input=pwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        console.print("[red]user-ops create failed: %s[/red]" % (r.stderr or r.stdout or ""))
        return
    console.print(Panel(
        f"[bold green]Account [bold white]{name}[/bold white] created.[/bold green]\n\n"
        "Use this account only for OpenClaw (isolated from your primary identity).",
        title="Isolated account ready",
        border_style="green",
    ))


def run_environment_checks() -> None:
    """Run all checks and print a table."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Detail")
    checks = [
        ("Homebrew (personalized preferred)", check_homebrew_personalized()),
        ("pyenv", check_pyenv()),
        ("Python 3.11", check_python_11()),
        ("nvm", check_nvm()),
        ("Node LTS (nvm)", check_nvm_lts()),
    ]
    for name, (ok, detail) in checks:
        status = "[green]OK[/green]" if ok else "[red]Missing[/red]"
        table.add_row(name, status, detail)
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("--checks", action="store_true", help="Run environment checks and exit")
    parser.add_argument("--warning", action="store_true", help="Show account isolation warning and exit")
    parser.add_argument("--setup", action="store_true", help="Set up all personalized deps (prompts for account consent)")
    parser.add_argument("--create-account", nargs="?", const=DEFAULT_OPENCLAW_USER, metavar="USER", help="Create isolated macOS user (default: openclaw); warns if exists")
    args = parser.parse_args()

    if args.checks:
        run_environment_checks()
        return
    if args.warning:
        show_account_warning()
        return
    if args.setup:
        setup_all_personalized()
        return
    if args.create_account is not None:
        create_isolated_account(args.create_account)
        return

    try:
        _cu = getpass.getuser()
        _uid = os.getuid()
    except Exception:
        _cu = os.environ.get("USER", "—")
        _uid = "—"
    _openclaw_exists = _account_exists_local(DEFAULT_OPENCLAW_USER)
    _account_line = "Account [bold]'%s'[/bold]: [green]exists[/green]" if _openclaw_exists else "Account [bold]'%s'[/bold]: [dim]not found[/dim]"
    _account_line = _account_line % DEFAULT_OPENCLAW_USER
    console.print(Panel(
        "Current user: [bold]%s[/bold] (UID: [bold]%s[/bold])\n"
        "%s\n\n"
        "%s" % (_cu, _uid, _account_line, TOOL_DESCRIPTION),
        title=TOOL_TITLE,
        border_style="blue",
    ))

    menu = [
        {"title": "Create isolated account (default: openclaw)", "value": "create_account"},
        {"title": "Run environment checks", "value": "checks"},
        {"title": "Show account isolation warning", "value": "warning"},
        {"title": "Set up all personalized deps (after consent)", "value": "setup"},
        {"title": "[Quit]", "value": "quit"},
    ]
    choices = format_menu_choices(menu)
    while True:
        choice = prompt_toolkit_menu(choices)
        if choice is None or choice == "quit":
            return
        if choice == "checks":
            run_environment_checks()
        elif choice == "warning":
            show_account_warning()
        elif choice == "setup":
            setup_all_personalized()
        elif choice == "create_account":
            create_isolated_account(DEFAULT_OPENCLAW_USER)


if __name__ == "__main__":
    main()
