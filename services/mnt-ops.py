#!/usr/bin/env python3
"""
@pas-executable
A comprehensive utility to manage remote mounts and syncing, defaulting to rclone with Cloudflare support.

This tool provides both a guided interactive TUI and a non-interactive CLI for:
- Configuring rclone remotes with Cloudflare Access ProxyCommand.
- Mounting remote filesystems to ~/pas-mounts/.
- Unmounting existing mount points.
- Performing rclone sync operations.

Usage:
  mnt-ops [options]

Options:
  --setup NAME --host HOSTNAME [--user USER]
      Configure a new rclone remote for a Cloudflare-protected host.
  --mount NAME
      Mount a configured rclone remote.
  --unmount NAME
      Unmount a previously mounted remote.
  --sync SRC DEST
      Perform an rclone sync between source and destination.
  --list
      List configured rclone remotes and active mounts.

If no options are provided, the script enters interactive mode.
"""

import os
import sys
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    load_pas_config,
    save_pas_config,
    prompt_yes_no,
    format_menu_choices,
    prompt_toolkit_menu,
    run_command,
    detect_cloudflared_binary
)
from rich.panel import Panel
from rich.table import Table

# --- Configuration Consolidation ---
MOUNT_BASE_DIR = Path.home() / "pas-mounts"
DEFAULT_ENGINE = "rclone"
RCLONE_CONFIG_PATH = Path.home() / ".config" / "rclone" / "rclone.conf"
# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "mnt-ops"
TOOL_TITLE = "Mount Operations (mnt-ops)"
TOOL_SHORT_DESC = "Manage remote mounts and syncing; rclone with Cloudflare Tunnel support."
TOOL_DESCRIPTION = "Manage remote mounts and syncing (mnt-ops). rclone-based with Cloudflare Tunnel support; unified mount point at ~/pas-mounts/."
# -----------------------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        f"[bold cyan]mnt-ops[/bold cyan]: {TOOL_DESCRIPTION}\n\n"
        f"[bold]Capabilities:[/bold]\n"
        f"• [bold]Default Engine:[/bold] {DEFAULT_ENGINE}\n"
        f"• [bold]Cloudflare Integration:[/bold] Automatically configures ProxyCommand for Tunnels.\n"
        f"• [bold]Mount Management:[/bold] Unified mount point at `{MOUNT_BASE_DIR}`.\n"
        f"• [bold]Non-Interactive:[/bold] Full CLI support for automation.\n\n"
        f"[bold]Requirements:[/bold]\n"
        f"• `rclone` must be installed.\n"
        f"• `cloudflared` (for Cloudflare-protected hosts)."
    )
    console.print(Panel(summary, title=TOOL_TITLE, expand=False))

def check_rclone_installed() -> bool:
    """Verify rclone is installed, offer to install via Homebrew if missing."""
    which = shutil.which("rclone")
    if not which:
        console.print("[bold red]Error: rclone not found.[/bold red]")
        if prompt_yes_no("Would you like to install rclone via Homebrew?"):
            console.print("Installing rclone...")
            res = subprocess.run(["brew", "install", "rclone"], check=False)
            if res.returncode == 0:
                console.print("[green]rclone installed successfully![/green]")
                return True
            else:
                console.print("[red]Failed to install rclone.[/red]")
                return False
        return False
    return True

def get_rclone_remotes() -> List[str]:
    """List configured rclone remotes."""
    res = run_command(["rclone", "listremotes"])
    if res.returncode == 0:
        return [r.strip().rstrip(":") for r in res.stdout.splitlines() if r.strip()]
    return []

def get_active_mounts() -> List[Dict[str, str]]:
    """List active mounts under MOUNT_BASE_DIR."""
    mounts = []
    if not MOUNT_BASE_DIR.exists():
        return mounts
    
    # Simple check: directories in MOUNT_BASE_DIR that are mount points
    for item in MOUNT_BASE_DIR.iterdir():
        if item.is_dir():
            # On macOS/Linux, we can check if it's a mount point
            res = run_command(["mount"])
            if str(item) in res.stdout:
                mounts.append({"name": item.name, "path": str(item)})
    return mounts

def setup_remote(name: str, host: str, user: str = "root"):
    """Configure a new rclone remote with Cloudflare ProxyCommand."""
    cloudflared_path = detect_cloudflared_binary()
    if not cloudflared_path:
        console.print("[bold red]Error:[/bold red] 'cloudflared' binary not found. Cannot configure ProxyCommand.")
        return False

    proxy_cmd = f"{cloudflared_path} access ssh --hostname %h"
    
    console.print(f"Configuring rclone remote [bold]{name}[/bold] for [bold]{host}[/bold]...")
    
    cmd = [
        "rclone", "config", "create", name, "sftp",
        "host", host,
        "user", user,
        "port", "22",
        "proxy_command", proxy_cmd
    ]
    
    res = run_command(cmd)
    if res.returncode == 0:
        console.print(f"[green][✓] Remote '{name}' configured successfully.[/green]")
        return True
    else:
        console.print(f"[red][ ] Failed to configure remote: {res.stderr}[/red]")
        return False

def mount_remote(name: str):
    """Mount an rclone remote to MOUNT_BASE_DIR."""
    mount_path = MOUNT_BASE_DIR / name
    mount_path.mkdir(parents=True, exist_ok=True)
    
    console.print(f"Mounting [bold]{name}:[/bold] to [bold]{mount_path}[/bold]...")
    
    # Run in background
    cmd = [
        "rclone", "mount", f"{name}:", str(mount_path),
        "--vfs-cache-mode", "full",
        "--daemon"
    ]
    
    res = run_command(cmd)
    if res.returncode == 0:
        console.print(f"[green][✓] Mounted successfully.[/green]")
        return True
    else:
        console.print(f"[red][ ] Mount failed: {res.stderr}[/red]")
        return False

def unmount_remote(name: str):
    """Unmount a remote."""
    mount_path = MOUNT_BASE_DIR / name
    if not mount_path.exists():
        console.print(f"[yellow]Mount point {mount_path} does not exist.[/yellow]")
        return False
    
    console.print(f"Unmounting [bold]{mount_path}[/bold]...")
    
    # Use umount or diskutil unmount based on OS
    if sys.platform == "darwin":
        cmd = ["diskutil", "unmount", "force", str(mount_path)]
    else:
        cmd = ["fusermount", "-u", str(mount_path)]
        
    res = run_command(cmd)
    if res.returncode == 0:
        console.print(f"[green][✓] Unmounted successfully.[/green]")
        # Clean up empty dir
        try:
            mount_path.rmdir()
        except Exception:
            pass
        return True
    else:
        console.print(f"[red][ ] Unmount failed: {res.stderr}[/red]")
        return False

def interactive_mode():
    """Guided TUI for mnt-ops."""
    show_summary()
    
    if not check_rclone_installed():
        return

    while True:
        remotes = get_rclone_remotes()
        active = get_active_mounts()
        
        menu_items = []
        
        # 1. Setup
        menu_items.append({"title": "Setup new Cloudflare remote", "value": "setup"})
        
        # 2. Mount
        if remotes:
            menu_items.append({"title": "Mount a remote", "value": "mount"})
            
        # 3. Unmount
        if active:
            menu_items.append({"title": "Unmount a remote", "value": "unmount"})
            
        # 4. Sync
        menu_items.append({"title": "Sync files (rclone sync)", "value": "sync"})
        
        # 5. List
        menu_items.append({"title": "List remotes and active mounts", "value": "list"})
        
        menu_items.append({"title": "[Quit]", "value": "quit"})
        
        formatted = format_menu_choices(menu_items, title_field="title", value_field="value")
        console.print("\n[bold]Main Menu:[/bold]")
        choice = prompt_toolkit_menu(formatted)
        
        if not choice or choice == "quit":
            break
            
        if choice == "setup":
            # Discover hosts from cf.json
            cf_config = load_pas_config("cf")
            # This is a heuristic: looking for hostnames in ingress rules or cached hosts
            # For now, let's just ask the user or show what we might know
            console.print("\n[bold]Setup Remote[/bold]")
            host = input("Enter Cloudflare hostname (e.g. host-ssh.example.com): ").strip()
            if not host: continue
            
            name = input(f"Enter remote name for rclone [{host.split('.')[0]}]: ").strip() or host.split('.')[0]
            user = input("Enter remote user [root]: ").strip() or "root"
            
            setup_remote(name, host, user)
            
        elif choice == "mount":
            if not remotes:
                console.print("[yellow]No remotes configured.[/yellow]")
                continue
            
            remote_choices = [{"title": r, "value": r} for r in remotes]
            remote_choices.append({"title": "[Back]", "value": "back"})
            formatted_remotes = format_menu_choices(remote_choices, title_field="title", value_field="value")
            console.print("\n[bold]Select remote to mount:[/bold]")
            selected = prompt_toolkit_menu(formatted_remotes)
            
            if selected and selected != "back":
                mount_remote(selected)
                
        elif choice == "unmount":
            if not active:
                console.print("[yellow]No active mounts.[/yellow]")
                continue
            
            active_choices = [{"title": a["name"], "value": a["name"]} for a in active]
            active_choices.append({"title": "[Back]", "value": "back"})
            formatted_active = format_menu_choices(active_choices, title_field="title", value_field="value")
            console.print("\n[bold]Select mount to unmount:[/bold]")
            selected = prompt_toolkit_menu(formatted_active)
            
            if selected and selected != "back":
                unmount_remote(selected)
                
        elif choice == "list":
            # Show Table
            table = Table(title="Rclone Remotes")
            table.add_column("Name", style="cyan")
            for r in remotes:
                table.add_row(r)
            console.print(table)
            
            if active:
                table_mounts = Table(title="Active Mounts")
                table_mounts.add_column("Name", style="green")
                table_mounts.add_column("Path", style="dim")
                for a in active:
                    table_mounts.add_row(a["name"], a["path"])
                console.print(table_mounts)
            else:
                console.print("[dim]No active mounts found under pas-mounts.[/dim]")
                
        elif choice == "sync":
            console.print("\n[bold]Sync Shortcut[/bold]")
            src = input("Enter source (e.g. remote:path or /local/path): ").strip()
            dest = input("Enter destination (e.g. remote:path or /local/path): ").strip()
            if src and dest:
                console.print(f"Running: rclone sync {src} {dest} -P")
                subprocess.run(["rclone", "sync", src, dest, "-P"])

def main():
    parser = argparse.ArgumentParser(
        description=TOOL_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--setup", help="Name for the new rclone remote")
    parser.add_argument("--host", help="Hostname for the new remote")
    parser.add_argument("--user", default="root", help="User for the new remote (default: root)")
    parser.add_argument("--mount", help="Name of the remote to mount")
    parser.add_argument("--unmount", help="Name of the mount to unmount")
    parser.add_argument("--sync", nargs=2, metavar=("SRC", "DEST"), help="Sync source to destination")
    parser.add_argument("--list", action="store_true", help="List remotes and active mounts")
    
    args = parser.parse_args()
    
    # Check if any args provided
    if any([args.setup, args.mount, args.unmount, args.sync, args.list]):
        if not check_rclone_installed():
            sys.exit(1)
            
        if args.setup:
            if not args.host:
                console.print("[red]Error: --host is required with --setup.[/red]")
                sys.exit(1)
            setup_remote(args.setup, args.host, args.user)
        
        if args.mount:
            mount_remote(args.mount)
            
        if args.unmount:
            unmount_remote(args.unmount)
            
        if args.sync:
            src, dest = args.sync
            console.print(f"Syncing {src} to {dest}...")
            subprocess.run(["rclone", "sync", src, dest, "-P"])
            
        if args.list:
            remotes = get_rclone_remotes()
            active = get_active_mounts()
            console.print(f"[bold]Remotes:[/bold] {', '.join(remotes) if remotes else 'None'}")
            if active:
                console.print("[bold]Active Mounts:[/bold]")
                for a in active:
                    console.print(f"  - {a['name']} -> {a['path']}")
            else:
                console.print("[bold]Active Mounts:[/bold] None")
    else:
        interactive_mode()

if __name__ == "__main__":
    main()
