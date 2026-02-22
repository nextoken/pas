#!/usr/bin/env python3
"""
@pas-executable
System cleanup utility to clear unnecessary files and caches.
"""

import sys
import os
import subprocess
import argparse
import re
from pathlib import Path
from typing import List, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    prompt_yes_no,
    prompt_toolkit_menu,
    format_menu_choices,
    run_command
)
from rich.panel import Panel

# --- Configuration ---
# ---------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]clean-ops[/bold cyan] is a system maintenance utility.\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Homebrew:[/bold] Clears old versions, downloads, and unused dependencies.\n"
        "• [bold]Safety:[/bold] Always prompts before performing destructive operations.\n"
        "• [bold]Extensible:[/bold] Designed to add more cleanup targets (npm, docker, etc.)."
    )
    console.print(Panel(summary, title="System Cleanup Utility", expand=False))

def cleanup_homebrew(dry_run: bool = False):
    """Perform Homebrew cleanup operations."""
    console.print(f"\n[bold cyan]Homebrew Cleanup {'(Dry Run)' if dry_run else ''}[/bold cyan]")
    
    if dry_run:
        console.print("\n[dim]> brew cleanup --prune=0 -n[/dim]")
        res = subprocess.run(["brew", "cleanup", "--prune=0", "-n"], capture_output=True, text=True)
        output = res.stdout + res.stderr
        
        # Try to find space information in output
        # Homebrew output usually looks like: "This operation would free approximately 1.2GB of disk space."
        space_match = re.search(r"would free approximately (.*?) of disk space", output)
        if space_match:
            space_saved = space_match.group(1)
            console.print(f"\n[bold green]Estimated space to be released: {space_saved}[/bold green]")
        else:
            # Fallback: show the list of what would be removed if no summary found
            if output.strip():
                console.print("\n[yellow]Items to be removed:[/yellow]")
                for line in output.splitlines():
                    if line.startswith("Would remove"):
                        console.print(f"  {line}")
            else:
                console.print("\n[green]No cleanup needed for Homebrew.[/green]")
        return

    # 1. brew cleanup
    console.print("\n[dim]> brew cleanup[/dim]")
    if prompt_yes_no("Run 'brew cleanup' to remove old versions and downloads?", default=True):
        subprocess.run(["brew", "cleanup"])
    
    # 2. brew autoremove
    console.print("\n[dim]> brew autoremove[/dim]")
    if prompt_yes_no("Run 'brew autoremove' to remove unused dependencies?", default=True):
        subprocess.run(["brew", "autoremove"])
    
    console.print("\n[green]Homebrew cleanup finished.[/green]")

def main_menu():
    """Main interactive menu."""
    while True:
        menu_items = [
            {"title": "Homebrew Cleanup (Dry Run - Preview Space)", "value": "brew_dry"},
            {"title": "Homebrew Cleanup (Actual)", "value": "brew"},
            {"title": "[Quit]", "value": "quit"}
        ]
        
        formatted = format_menu_choices(menu_items, title_field="title", value_field="value")
        console.print("\n[bold cyan]Select Cleanup Target:[/bold cyan]")
        choice = prompt_toolkit_menu(formatted)
        
        if not choice or choice == "quit":
            break
            
        if choice == "brew_dry":
            cleanup_homebrew(dry_run=True)
        elif choice == "brew":
            cleanup_homebrew(dry_run=False)

def main():
    parser = argparse.ArgumentParser(description="System cleanup utility")
    args = parser.parse_args()
    
    show_summary()
    main_menu()

if __name__ == "__main__":
    main()
