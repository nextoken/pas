#!/usr/bin/env python3
"""
@pas-executable
Centralized bridge for GitHub operations (Init, Reset, Transfer, Access, Actions).
"""
import sys
import os
from pathlib import Path
import subprocess

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import console, format_menu_choices, prompt_toolkit_menu
from rich.panel import Panel

def main():
    scripts_dir = Path(__file__).resolve().parent
    
    # Define available tools
    tools = [
        {
            "name": "Init Repository",
            "script": "gh-init-repo.py",
            "desc": "Initialize a new GitHub repository from current folder"
        },
        {
            "name": "Reset History",
            "script": "gh-reset-repo.py",
            "desc": "Destructively wipe and restart git history on GitHub"
        },
        {
            "name": "Transfer Ownership",
            "script": "gh-transfer-repo.py",
            "desc": "Move a repository to another organization or user"
        },
        {
            "name": "Grant Server Access",
            "script": "gh-repo-access-grant.py",
            "desc": "Setup Deploy Keys to grant a remote server access"
        },
        {
            "name": "Manage Git Keys",
            "script": "git-use-key.py",
            "desc": "Select and set SSH keys for local git operations"
        },
        {
            "name": "Setup Actions CI/CD",
            "script": "gh-actions-secrets-vars-setup.py",
            "desc": "Configure GitHub Actions secrets and variables"
        },
        {
            "name": "Create Organization",
            "script": "gh-create-org.py",
            "desc": "Guided discovery for creating a new GitHub Organization"
        }
    ]

    info_text = """
[bold]GitHub Operations Hub[/bold]

Centralized access to all GitHub-related automation tools:
- [cyan]Lifecycle[/cyan]: Initialize new projects, reset history, or create organizations.
- [cyan]Governance[/cyan]: Transfer ownership between users and organizations.
- [cyan]Security[/cyan]: Grant server access via Deploy Keys.
- [cyan]Automation[/cyan]: Setup CI/CD secrets and variables for GitHub Actions.
"""
    console.print(Panel(info_text.strip(), title="gh-ops", border_style="blue"))
    console.print("\n")

    menu_choices = []
    for tool in tools:
        menu_choices.append({
            "title": f"{tool['name']:<25} | {tool['desc']}",
            "value": tool['script']
        })
    
    menu_choices.append({"title": "[Quit]", "value": "QUIT"})
    
    formatted_choices = format_menu_choices(menu_choices, title_field="title", value_field="value")
    console.print("[bold cyan]Select GitHub Operation:[/bold cyan]")
    selected_script = prompt_toolkit_menu(formatted_choices)
    
    if not selected_script or selected_script == "QUIT":
        return

    script_path = scripts_dir / selected_script
    if not script_path.exists():
        console.print(f"[bold red]Error:[/bold red] Script not found: {selected_script}")
        sys.exit(1)

    # Execute the selected script
    try:
        # Pass through any arguments if needed, though most bridge use cases are interactive
        subprocess.run([sys.executable, str(script_path)] + sys.argv[1:], check=False)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[bold red]Error executing {selected_script}:[/bold red] {e}")

if __name__ == "__main__":
    main()
