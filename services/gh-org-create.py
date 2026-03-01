#!/usr/bin/env python3
"""
@pas-executable
Guided discovery for creating a new GitHub Organization.
"""

import sys
import webbrowser
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import console, prompt_yes_no
from rich.panel import Panel

# --- Configuration URLs ---
GH_NEW_ORG_URL = "https://github.com/organizations/plan"
# --------------------------

def main():
    info_text = f"""
[bold]GitHub Organization Creation[/bold]

GitHub does not currently allow organization creation via the CLI or API for individual accounts.
This process must be completed through the GitHub web interface to select a plan and configure billing.

[cyan]Process:[/cyan]
1. Select a plan (Free, Team, or Enterprise).
2. Choose an organization name and contact email.
3. Verify your identity if prompted.

The setup page can be found at:
[bold blue]{GH_NEW_ORG_URL}[/bold blue]
"""
    console.print(Panel(info_text.strip(), title="gh-org-create", border_style="blue"))
    console.print("\n")

    if prompt_yes_no("Would you like to open the GitHub Organization setup page in your browser?", default=True):
        console.print(f"Opening [cyan]{GH_NEW_ORG_URL}[/cyan]...")
        webbrowser.open(GH_NEW_ORG_URL)
    else:
        console.print("[yellow]Action skipped.[/yellow] You can visit the link manually when ready.")

if __name__ == "__main__":
    main()
