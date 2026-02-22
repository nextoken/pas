#!/usr/bin/env python3
"""
@pas-executable
Select and set an existing private key from ~/.ssh/ for git.
Use case: Manage multiple Git identities or project-specific keys (like deploy keys) without changing global SSH config.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel
import questionary

from helpers.core import (
    get_ssh_keys, 
    get_git_info, 
    run_command, 
    format_menu_choices, 
    prompt_toolkit_menu,
    copy_to_clipboard
)

console = Console()

def handle_git_config(selected_key: Path, repo_root: str):
    """Set the git config for the selected repository."""
    abs_key_path = str(selected_key.absolute())
    ssh_cmd_str = f"ssh -i {abs_key_path} -o IdentitiesOnly=yes"
    
    cmd = ["git", "config", "core.sshCommand", ssh_cmd_str]
    console.print(f"Running: [dim]{' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    
    if result.returncode == 0:
        console.print(f"[green]Successfully set git SSH key to {selected_key.name}[/green]")
        return True
    else:
        console.print(f"[red]Error setting git config: {result.stderr}[/red]")
        return False

def handle_clone(selected_key: Path, clone_url: str, target_dir: Optional[str] = None):
    """Clone a repository using the selected key."""
    abs_key_path = str(selected_key.absolute())
    ssh_cmd_str = f"ssh -i {abs_key_path} -o IdentitiesOnly=yes"
    
    clone_cmd = ["git", "clone", clone_url]
    if target_dir:
        clone_cmd.append(target_dir)
    
    console.print(f"Cloning with key: [bold]{selected_key.name}[/bold]...")
    
    # Use GIT_SSH_COMMAND environment variable for clone
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = ssh_cmd_str
    
    result = subprocess.run(clone_cmd, env=env)
    
    if result.returncode == 0:
        # After successful clone, if we know where it cloned, 
        # we should set the local config in that new repo too
        target = target_dir or clone_url.split("/")[-1].replace(".git", "")
        target_path = Path(target)
        
        if target_path.exists() and (target_path / ".git").exists():
            console.print(f"Setting local config in {target}...")
            handle_git_config(selected_key, target)
        
        console.print("\n[green]Successfully cloned and configured repository.[/green]")
        return True
    else:
        console.print(f"\n[red]Error: Clone failed with exit code {result.returncode}[/red]")
        return False

GIT_SUBCOMMANDS_WITH_KEY = ("pull", "fetch", "push", "ls-remote")


def handle_git_command(selected_key: Path, repo_root: str, subcommand: str, extra_args: list) -> bool:
    """Set key for repo and run git subcommand (e.g. pull, fetch, push)."""
    if not handle_git_config(selected_key, repo_root):
        return False
    cmd = ["git", subcommand] + extra_args
    console.print(f"Running: [dim]{' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=repo_root)
    return result.returncode == 0


def test_connection(selected_key: Path, host: str = "github.com"):
    """Test SSH connection to a host using the selected key."""
    abs_key_path = str(selected_key.absolute())
    console.print(f"Testing connection to [bold]{host}[/bold] using [bold]{selected_key.name}[/bold]...")
    
    # -T to disable pseudo-terminal, -o IdentitiesOnly=yes to ensure we use only this key
    cmd = ["ssh", "-T", "-i", abs_key_path, "-o", "IdentitiesOnly=yes", f"git@{host}"]
    
    # GitHub returns exit code 1 even on success, but with a welcome message
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if "Hi " in result.stderr or "successfully authenticated" in result.stderr:
        console.print(f"[green]✅ Success:[/green]\n[dim]{result.stderr.strip()}[/dim]")
        return True
    else:
        console.print(f"[red]❌ Connection failed:[/red]\n[dim]{result.stderr or result.stdout}[/dim]")
        return False

def main():
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip() + "\n\nUsage:\n"
                    "  git-use-key                    # Set key for current repository\n"
                    "  git-use-key pull [args...]     # Pull with selected key (also: fetch, push, ls-remote)\n"
                    "  git-use-key <url> [dir]        # Clone a new repository with selected key",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("first", nargs="?", help="Git subcommand (pull/fetch/push) or clone URL")
    parser.add_argument("rest", nargs="*", help="Extra args for git subcommand or clone target dir")
    args = parser.parse_args()

    info_text = """
[bold]Git SSH Key Selector[/bold]

Manage project-specific SSH keys without changing global configuration:
- [cyan]Local Config[/cyan]: Sets core.sshCommand for the current repository.
- [cyan]Clone with Key[/cyan]: Clone a repo using a specific identity immediately.
- [cyan]Auto-Discovery[/cyan]: Scans ~/.ssh/ for available private keys.
"""
    console.print(Panel(info_text.strip(), title="git-use-key", border_style="blue"))
    console.print("\n")

    # If CLI args are provided, we follow the one-shot path
    if args.first:
        keys = get_ssh_keys()
        if not keys:
            console.print("[red]No private SSH keys found in ~/.ssh/[/red]")
            sys.exit(1)

        menu_items = [{"name": key.name, "path": key} for key in keys]
        menu_items.append({"name": "[Quit]", "path": "q"})
        choices = format_menu_choices(menu_items, title_field="name", value_field="path")

        if args.first in GIT_SUBCOMMANDS_WITH_KEY:
            git_info = get_git_info()
            if not git_info or not git_info.get("root"):
                console.print("[red]Not in a git repository. Run from a repo directory.[/red]")
                sys.exit(1)
            console.print(f"[bold]Select key to use for [cyan]git {args.first}[/cyan]:[/bold]")
            selected_key = prompt_toolkit_menu(choices)
            if not selected_key or selected_key == "q":
                return
            if not handle_git_command(selected_key, git_info["root"], args.first, args.rest):
                sys.exit(1)
        else:
            console.print(f"[bold]Select key to use for cloning [cyan]{args.first}[/cyan]:[/bold]")
            selected_key = prompt_toolkit_menu(choices)
            if not selected_key or selected_key == "q":
                return
            target_dir = args.rest[0] if args.rest else None
            if not handle_clone(selected_key, args.first, target_dir):
                sys.exit(1)
        return

    # Interactive TUI Loop
    while True:
        keys = get_ssh_keys()
        if not keys:
            console.print("[red]No private SSH keys found in ~/.ssh/[/red]")
            sys.exit(1)

        menu_items = [{"name": key.name, "path": key} for key in keys]
        menu_items.append({"name": "[Quit]", "path": "QUIT"})
        choices = format_menu_choices(menu_items, title_field="name", value_field="path")
        
        console.print("\n[bold]Select an SSH Key to manage:[/bold]")
        selected_key = prompt_toolkit_menu(choices)
        
        if not selected_key or selected_key == "QUIT":
            break

        # Action Menu for selected key
        while True:
            git_info = get_git_info()
            repo_root = git_info["root"] if git_info else None
            
            console.print(f"\n[bold cyan]Selected Key: {selected_key.name}[/bold cyan]")
            
            action_items = []
            if repo_root:
                action_items.append({"title": f"Set for current repository: [dim]{Path(repo_root).name}[/dim]", "value": "SET_CONFIG"})
            
            action_items.append({"title": "Clone a new repository...", "value": "CLONE"})
            action_items.append({"title": "Test connection (GitHub)", "value": "TEST_GH"})
            action_items.append({"title": "Test connection (GitLab)", "value": "TEST_GL"})
            action_items.append({"title": "[Back]", "value": "BACK"})
            action_items.append({"title": "[Quit]", "value": "QUIT"})
            
            action_choices = format_menu_choices(action_items, title_field="title", value_field="value")
            action = prompt_toolkit_menu(action_choices)
            
            if action == "QUIT":
                sys.exit(0)
            if not action or action == "BACK":
                break
            
            if action == "SET_CONFIG":
                handle_git_config(selected_key, repo_root)
            elif action == "CLONE":
                url = questionary.text("Enter repository URL to clone:").ask()
                if url:
                    handle_clone(selected_key, url)
            elif action == "TEST_GH":
                test_connection(selected_key, "github.com")
            elif action == "TEST_GL":
                test_connection(selected_key, "gitlab.com")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
