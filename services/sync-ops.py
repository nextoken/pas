#!/usr/bin/env python3
"""
@pas-executable
Smart rsync profile manager with Cloudflare tunnel detection and remote completion.

Local paths are normalized before calling rsync:
- shell-escaped spaces (e.g. My\\ Drive) are converted to real paths (My Drive)
- user home shortcuts (e.g. ~/Downloads) are expanded to absolute paths
so cloud-storage targets (e.g. Google Drive) work when profiles are passed to
subprocess without a shell. See _normalize_local_path().
"""

import sys
import os
import subprocess
import argparse
import re
from pathlib import Path
from typing import List, Optional, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    prompt_yes_no,
    prompt_toolkit_menu,
    format_menu_choices,
    load_pas_config,
    save_pas_config,
    run_command,
    is_cloudflare_host,
    detect_cloudflared_binary
)
from rich.panel import Panel
from rich.table import Table

from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion, PathCompleter

# --- Service Configuration ---
SYNC_CONFIG_SERVICE = "sync"
RSYNC_DEFAULT_ARGS = ["-avh", "--progress"]
RSYNC_DRY_RUN_ARGS = ["-n"]
# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "sync-ops"
TOOL_TITLE = "Rsync Profile Manager"
TOOL_SHORT_DESC = "Smart rsync profile manager with Cloudflare tunnel detection and remote completion."
TOOL_DESCRIPTION = "Smart rsync profile manager with Cloudflare tunnel detection and remote completion."
# -----------------------------

# --- System Paths ---
XSSH_PATH = Path(project_root) / "utils" / "xssh.py"
# --------------------

class RsyncPathCompleter(Completer):
    """
    Custom completer that handles local paths and remote user@host:path completion.
    """
    def __init__(self):
        self.local_completer = PathCompleter(expanduser=True)
        self.remote_cache = {} # Cache for remote listings: {(host, path): [items]}

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Remote completion: user@host:path
        if ":" in text:
            parts = text.split(":", 1)
            remote_prefix = parts[0]
            remote_path = parts[1]

            # We only attempt remote completion if we have at least the host
            if remote_prefix:
                # Find the directory we are completing in
                if "/" in remote_path:
                    last_slash_idx = remote_path.rfind("/")
                    dir_path = remote_path[:last_slash_idx + 1]
                    prefix = remote_path[last_slash_idx + 1:]
                else:
                    dir_path = ""
                    prefix = remote_path

                cache_key = (remote_prefix, dir_path)
                if cache_key not in self.remote_cache:
                    # Fetch remote listing
                    items = self._fetch_remote_listing(remote_prefix, dir_path)
                    self.remote_cache[cache_key] = items
                
                for item in self.remote_cache[cache_key]:
                    if item.startswith(prefix):
                        yield Completion(item, start_position=-len(prefix))
            return

        # Local completion
        for completion in self.local_completer.get_completions(document, complete_event):
            yield completion

    def _fetch_remote_listing(self, host: str, path: str) -> List[str]:
        """Fetch remote directory listing using ssh/xssh."""
        # Build command to list directory
        # -F adds / to directories, which is useful
        remote_cmd = f"ls -F -1 {path or '.'}"
        
        # We use a short timeout for responsiveness
        cmd = [sys.executable, str(XSSH_PATH), host, remote_cmd]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                return [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            pass
        return []

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        f"[bold cyan]sync-ops[/bold cyan]: {TOOL_DESCRIPTION}\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Profiles:[/bold] Save and reuse source/target pairs with optional names.\n"
        "• [bold]Smart Detection:[/bold] Uses `xssh` to handle Cloudflare Tunnels automatically.\n"
        "• [bold]Path Completion:[/bold] Tab-completion for both local and remote paths.\n"
        "• [bold]Validation:[/bold] Checks for common rsync pitfalls like trailing slashes.\n"
        "• [bold]Dry Run:[/bold] Always verify changes before execution."
    )
    console.print(Panel(summary, title=TOOL_TITLE, expand=False))

def validate_rsync_paths(source: str, target: str):
    """Provide warnings for common rsync pitfalls."""
    warnings = []
    
    # Use normalized local path for existence/writable check (see _normalize_local_path).
    local_target = _normalize_local_path(target) if ":" not in target else None
    if local_target and os.path.isdir(local_target):
        if not os.access(local_target, os.W_OK):
            warnings.append(
                f"[bold red]Permission:[/bold red] Target directory exists but is not writable.\n"
                f"  {local_target}\n"
                f"If this is Google Drive / cloud storage, ensure the folder is available and Terminal has access."
            )
    
    # Pitfall: Trailing slash on source directory
    # source/ vs source
    if not source.endswith("/") and not os.path.isfile(source) and ":" not in source:
        if os.path.isdir(source):
            warnings.append(
                f"[yellow]Warning:[/yellow] Source '{source}' is a directory without a trailing slash.\n"
                f"rsync will create a directory '{os.path.basename(source)}' inside the target."
            )
    
    # Remote vs Remote
    if ":" in source and ":" in target:
        warnings.append("[bold red]Note:[/bold red] Syncing between two remote hosts is usually not supported by standard rsync.")

    if warnings:
        console.print("\n" + "\n".join(warnings))
        return prompt_yes_no("Proceed with these paths?")
    return True

def _normalize_local_path(path: str) -> str:
    """
    Normalize a local path for use with rsync/subprocess.

    Profiles may store paths with shell-style escaping (e.g. "My\\ Drive" for
    Google Drive). We pass args to rsync via subprocess without a shell, so
    those backslashes are passed literally. Rsync then treats the path as
    a directory named "My\\ Drive" instead of "My Drive", which can cause
    mkpath/permission errors on cloud storage (e.g. ~/Library/CloudStorage/...).

    This replaces "\\ " with " " for local paths only, so the real path on
    disk is used. Remote paths (containing ":") are returned unchanged.
    """
    if ":" in path:
        return path  # remote path, leave as-is

    # First un-escape spaces, then expand ~ to the user's home directory.
    cleaned = path.replace("\\ ", " ")
    return str(Path(cleaned).expanduser())


def get_rsync_command(source: str, target: str, dry_run: bool = False) -> List[str]:
    """Build the rsync command with smart SSH detection."""
    cmd = ["rsync"] + RSYNC_DEFAULT_ARGS
    
    if dry_run:
        cmd += RSYNC_DRY_RUN_ARGS
        
    # Check if we need xssh
    remote_host = None
    if ":" in source:
        remote_host = source.split(":")[0]
    elif ":" in target:
        remote_host = target.split(":")[0]
        
    if remote_host and is_cloudflare_host(remote_host):
        # We need to use the absolute path to xssh.py or ensure it's in PATH
        # Since it's a pas-executable, 'xssh' should be in ~/bin_pas
        cmd += ["-e", f"{sys.executable} {XSSH_PATH}"]
        
    # Normalize local paths (see _normalize_local_path) so e.g. My\ Drive -> My Drive.
    cmd += [_normalize_local_path(source), _normalize_local_path(target)]
    return cmd

def run_sync(source: str, target: str, profile_name: Optional[str] = None):
    """Execute the sync process."""
    if not validate_rsync_paths(source, target):
        return

    while True:
        # Dry Run first
        console.print(f"\n[bold cyan]Dry Run Phase:[/bold cyan]")
        dry_run_cmd = get_rsync_command(source, target, dry_run=True)
        console.print(f"Command: [dim]{' '.join(dry_run_cmd)}[/dim]\n")
        
        subprocess.run(dry_run_cmd)
        
        if not prompt_yes_no("\nDry run complete. Execute actual sync?", default=False):
            break
            
        console.print(f"\n[bold green]Executing Sync:[/bold green]")
        sync_cmd = get_rsync_command(source, target, dry_run=False)
        subprocess.run(sync_cmd)
        break

def manage_profiles():
    """Main menu for managing profiles."""
    while True:
        config = load_pas_config(SYNC_CONFIG_SERVICE)
        profiles = config.get("profiles", [])
        
        table = Table(title="Saved Profiles", show_lines=True)
        table.add_column("ID", style="dim", vertical="top")
        table.add_column("Details")
        
        for i, p in enumerate(profiles, 1):
            details = (
                f"[cyan]Name:[/cyan]   {p.get('name', 'N/A')}\n"
                f"[green]Source:[/green] {p['source']}\n"
                f"[yellow]Target:[/yellow] {p['target']}"
            )
            table.add_row(str(i), details)
            
        console.print(table)
        
        menu_items = []
        if profiles:
            menu_items.append({"title": "Run a profile", "value": "run"})
            menu_items.append({"title": "Delete a profile", "value": "delete"})
        
        menu_items.append({"title": "Create a new profile", "value": "new"})
        menu_items.append({"title": "One-off sync (no profile)", "value": "oneoff"})
        menu_items.append({"title": "[Quit]", "value": "quit"})
        
        formatted = format_menu_choices(menu_items, title_field="title", value_field="value")
        choice = prompt_toolkit_menu(formatted)
        
        if not choice or choice == "quit":
            break
            
        if choice == "new" or choice == "oneoff":
            completer = RsyncPathCompleter()
            console.print("\n[bold]Examples:[/bold]")
            console.print("  [green]Local:[/green]  /Users/username/Documents/ (trailing slash syncs contents)")
            console.print("  [yellow]Remote:[/yellow] user@host:/path/to/dir\n")
            
            source = prompt("Enter source: ", completer=completer).strip()
            if not source: continue
            
            target = prompt("Enter target: ", completer=completer).strip()
            if not target: continue
            
            name = None
            if choice == "new":
                name = input("Enter profile name (optional): ").strip()
                profiles.append({"name": name, "source": source, "target": target})
                config["profiles"] = profiles
                save_pas_config(SYNC_CONFIG_SERVICE, config)
                console.print("[green]Profile saved.[/green]")
            
            run_sync(source, target, name)
            
        elif choice == "run":
            profile_choices = [{"title": f"{p.get('name', 'Profile ' + str(i))}: {p['source']} -> {p['target']}", "value": i-1} 
                             for i, p in enumerate(profiles, 1)]
            profile_choices.append({"title": "[Back]", "value": "back"})
            
            p_idx = prompt_toolkit_menu(format_menu_choices(profile_choices, title_field="title", value_field="value"))
            if p_idx == "back" or p_idx is None:
                continue
                
            p = profiles[p_idx]
            completer = RsyncPathCompleter()
            
            console.print(f"\n[bold cyan]Confirm/Edit Profile[/bold cyan]")
            name = prompt("Name:   ", default=p.get("name", "")).strip()
            source = prompt("Source: ", default=p["source"], completer=completer).strip()
            target = prompt("Target: ", default=p["target"], completer=completer).strip()
            
            if name != p.get("name") or source != p["source"] or target != p["target"]:
                if prompt_yes_no("Save these changes to the profile?", default=True):
                    profiles[p_idx]["name"] = name
                    profiles[p_idx]["source"] = source
                    profiles[p_idx]["target"] = target
                    config["profiles"] = profiles
                    save_pas_config(SYNC_CONFIG_SERVICE, config)
                    console.print("[green]Profile updated.[/green]")
            
            run_sync(source, target, name)
            
        elif choice == "delete":
            profile_choices = [{"title": f"{p.get('name', 'Profile ' + str(i))}", "value": i-1} 
                             for i, p in enumerate(profiles, 1)]
            profile_choices.append({"title": "[Back]", "value": "back"})
            
            p_choice = prompt_toolkit_menu(format_menu_choices(profile_choices, title_field="title", value_field="value"))
            if p_choice == "back" or p_choice is None:
                continue
                
            if prompt_yes_no(f"Are you sure you want to delete profile '{profiles[p_choice].get('name', 'N/A')}'?"):
                profiles.pop(p_choice)
                config["profiles"] = profiles
                save_pas_config(SYNC_CONFIG_SERVICE, config)
                console.print("[red]Profile deleted.[/red]")

def main():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    args = parser.parse_args()
    
    show_summary()
    manage_profiles()

if __name__ == "__main__":
    main()
