#!/usr/bin/env python3
"""
@pas-executable
Link a local machine to a remote headless user by importing SSH keys and registering xssh profiles.
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    prompt_yes_no,
    format_menu_choices,
    prompt_toolkit_menu,
    run_command,
    load_pas_config,
    save_pas_config
)
from rich.panel import Panel

# --- Configuration ---
SSHS_CONFIG_SERVICE = "sshs"
# ---------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]xssh-link[/bold cyan] automates the linking of local PAS to remote headless accounts.\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Remote Discovery:[/bold] Finds headless user keys on a remote master Mac.\n"
        "• [bold]Secure Import:[/bold] Uses `xscp` to pull private keys to your local machine.\n"
        "• [bold]Profile Registration:[/bold] Automatically updates `xssh` profiles for zero-config login.\n"
        "• [bold]Tunnel Mapping:[/bold] Links the imported key to a Cloudflare Tunnel hostname."
    )
    console.print(Panel(summary, title="xssh-link: Credential Importer", expand=False))

def get_remote_keys(remote_host: str) -> List[str]:
    """Find id_ed25519_* keys in the remote's ~/.ssh/ directory."""
    console.print(f"Discovering headless user keys on [bold]{remote_host}[/bold]...")
    
    # Run ls via xssh to get the list of keys
    # We look for id_ed25519_ but exclude common system names
    cmd = ["xssh", remote_host, "ls -1 ~/.ssh/id_ed25519_* 2>/dev/null"]
    res = run_command(cmd)
    
    if res.returncode != 0:
        return []
    
    keys = []
    ignore_list = ["id_ed25519_dev", "id_ed25519_automation", "id_ed25519_remote"]
    
    for line in res.stdout.splitlines():
        key_name = line.split("/")[-1]
        if key_name not in ignore_list and not key_name.endswith(".pub"):
            keys.append(key_name)
            
    return keys

def link_user(remote_host: str, key_name: str):
    """Import the key and register the xssh profile."""
    # Extract username from key name: id_ed25519_username
    username = key_name.replace("id_ed25519_", "")
    
    local_key_path = Path.home() / ".ssh" / f"{key_name}_remote"
    
    console.print(f"\nLinking user [bold]{username}[/bold] from {remote_host}...")
    
    # 1. Pull the key using xscp
    if local_key_path.exists():
        if not prompt_yes_no(f"Local key {local_key_path.name} already exists. Overwrite?", default=False):
            return
            
    console.print(f"Importing private key via [bold]xscp[/bold]...")
    import_cmd = ["xscp", f"{remote_host}:~/.ssh/{key_name}", str(local_key_path)]
    res = run_command(import_cmd, capture_output=False)
    
    if res.returncode != 0:
        console.print("[bold red]Error:[/bold red] Failed to import key.")
        return
        
    local_key_path.chmod(0o600)
    
    # 2. Get Tunnel Hostname
    tunnel_host = input(f"Enter the Cloudflare Tunnel hostname for {username} (e.g. {username}.example.com): ").strip()
    if not tunnel_host:
        console.print("[yellow]No tunnel hostname provided. Profile will not be registered.[/yellow]")
        return
        
    # 3. Register xssh profile
    config = load_pas_config(SSHS_CONFIG_SERVICE)
    profiles = config.get("profiles", {})
    
    target = f"{username}@{tunnel_host}"
    profiles[target] = {
        "identity_file": str(local_key_path.resolve()),
        "remote_user": username,
        "tunnel_host": tunnel_host,
        "source_host": remote_host
    }
    
    config["profiles"] = profiles
    save_pas_config(SSHS_CONFIG_SERVICE, config)
    
    console.print(f"\n[bold green]Success![/bold green] User {username} is now linked.")
    console.print(f"You can now login with: [bold]xssh {target}[/bold]")

def main():
    parser = argparse.ArgumentParser(description="Link local machine to remote headless accounts.")
    parser.add_argument("remote_host", nargs="?", help="The remote master Mac to discover keys from")
    parser.add_argument("username", nargs="?", help="Specific username to link")
    
    args = parser.parse_args()
    
    if not args.remote_host:
        show_summary()
        console.print("\n[bold yellow]Usage:[/bold yellow] xssh-link <remote-host> [username]")
        sys.exit(1)
        
    remote_host = args.remote_host
    
    # 1. Discover keys
    keys = get_remote_keys(remote_host)
    
    if not keys:
        console.print(f"[yellow]No headless user keys found on {remote_host}.[/yellow]")
        console.print("Ensure the user was created with [bold]mac-user[/bold] and keys were setup.")
        sys.exit(0)
        
    # 2. Selection
    selected_key = None
    if args.username:
        expected_key = f"id_ed25519_{args.username}"
        if expected_key in keys:
            selected_key = expected_key
        else:
            console.print(f"[red]Key for user '{args.username}' not found on {remote_host}.[/red]")
            # Fallback to menu
            
    if not selected_key:
        if len(keys) == 1:
            if prompt_yes_no(f"Found one headless user: [bold]{keys[0].replace('id_ed25519_', '')}[/bold]. Link it?", default=True):
                selected_key = keys[0]
        else:
            choices = [{"title": k.replace("id_ed25519_", ""), "value": k} for k in keys]
            choices.append({"title": "[Quit]", "value": "quit"})
            
            console.print("\n[bold]Select a headless user to link:[/bold]")
            selected_key = prompt_toolkit_menu(format_menu_choices(choices))
            
    if selected_key and selected_key != "quit":
        link_user(remote_host, selected_key)

if __name__ == "__main__":
    main()
