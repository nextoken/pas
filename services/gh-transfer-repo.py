#!/usr/bin/env python3
"""
@pas-executable
Transfer repository ownership to another user or organization on GitHub, or sync local remote URLs after a transfer.
Use case: 
1. Move a project from a personal account to an organization or another user.
2. Automatically detect and fix (sync) local remote URLs on other machines after a repository has been moved.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel

from helpers.core import get_git_info, run_command, prompt_yes_no, check_gh_auth, format_menu_choices, prompt_toolkit_menu

console = Console()

def get_possible_owners() -> list[str]:
    """Get list of possible owners (user and their orgs)."""
    owners = []
    # Get current user
    res = run_command(["gh", "api", "user", "-q", ".login"])
    if res.returncode == 0:
        owners.append(res.stdout.strip())
    
    # Get orgs
    res = run_command(["gh", "api", "user/orgs", "-q", ".[].login"])
    if res.returncode == 0:
        orgs = res.stdout.strip().splitlines()
        owners.extend([o for o in orgs if o])
    
    return owners

def main():
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sync", action="store_true", help="Only check for redirects and sync local remote")
    parser.add_argument("--target", help="Skip selection and transfer to this user/org")
    args = parser.parse_args()

    info_text = """
[bold]GitHub Repository Transfer & Sync[/bold]

Manage repository ownership and local remote updates:
- [cyan]Transfer[/cyan]: Move a repo from a personal account to an org or another user.
- [cyan]Auto-Sync[/cyan]: Automatically detects if a repo has moved and updates local remote URLs.
- [cyan]Interactive Choice[/cyan]: Fetches available organizations for easy selection.
"""
    console.print(Panel(info_text.strip(), title="gh-transfer-repo", border_style="blue"))
    console.print("\n")

    if not check_gh_auth():
        print("\nError: You are not authenticated with GitHub CLI.")
        print("Please run 'gh auth login' first.")
        sys.exit(1)

    git_info = get_git_info()
    if not git_info or not git_info['origin']:
        print("\nError: Current directory is not a git repository with a remote origin.")
        sys.exit(1)

    origin_url = git_info['origin']
    # Extract current owner and name
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?", origin_url)
    if not match:
        print(f"\nError: Could not parse GitHub owner and name from origin URL: {origin_url}")
        sys.exit(1)
    
    current_owner, repo_name = match.groups()
    current_full_name = f"{current_owner}/{repo_name}"

    print(f"Current repository (local): {current_full_name}")

    # --- Check for Redirects/Sync ---
    print("Checking GitHub for repository status...")
    res = run_command(["gh", "repo", "view", current_full_name, "--json", "nameWithOwner"])
    
    if res.returncode == 0:
        import json
        try:
            data = json.loads(res.stdout)
            api_full_name = data.get("nameWithOwner")
            
            if api_full_name and api_full_name.lower() != current_full_name.lower():
                print(f"\n[!] REDIRECT DETECTED: This repository has been moved to '{api_full_name}'.")
                if prompt_yes_no(f"Would you like to sync your local remote URL to the new location?", default=True):
                    new_owner = api_full_name.split("/")[0]
                    if origin_url.startswith("git@"):
                        new_url = f"git@github.com:{api_full_name}.git"
                    else:
                        new_url = f"https://github.com/{api_full_name}.git"
                    
                    print(f"Updating remote origin to: {new_url}")
                    run_command(["git", "remote", "set-url", "origin", new_url])
                    print("Remote URL updated successfully.")
                    
                    if args.sync:
                        sys.exit(0)
                        
                    if not prompt_yes_no("Would you like to proceed with a NEW transfer to another owner?", default=False):
                        sys.exit(0)
                    else:
                        current_owner = new_owner
                        current_full_name = api_full_name
            else:
                print("Local remote is consistent with GitHub.")
        except json.JSONDecodeError:
            pass
    else:
        print(f"[yellow]Warning: Could not reach GitHub repository '{current_full_name}'.[/yellow]")
        print("The remote URL might be incorrect or the repository may not exist.")
        if not prompt_yes_no("Do you want to continue anyway?", default=False):
            sys.exit(1)

    if args.sync:
        print("Sync complete.")
        sys.exit(0)

    # --- Normal Transfer Flow ---
    target_owner = args.target
    if not target_owner:
        # Fetch possible target owners
        print("Fetching available organizations...")
        owners = get_possible_owners()
        # Filter out current owner
        targets = [o for o in owners if o.lower() != current_owner.lower()]

        if not targets:
            print("\nNo other organizations found. You might need to enter the target manually.")
            target_owner = input("Enter target user or organization name: ").strip()
        else:
            menu_choices = []
            for owner in targets:
                menu_choices.append({"title": owner, "value": owner})
            menu_choices.append({"title": "[Enter manually]", "value": "MANUAL"})
            menu_choices.append({"title": "[Quit]", "value": "QUIT"})
            
            formatted_choices = format_menu_choices(menu_choices, title_field="title", value_field="value")
            console.print("\n[bold cyan]Select target owner:[/bold cyan]")
            selection = prompt_toolkit_menu(formatted_choices)
            
            if not selection or selection == "QUIT":
                sys.exit(0)
            elif selection == "MANUAL":
                target_owner = input("Enter target owner name: ").strip()
            else:
                target_owner = selection

    if not target_owner:
        print("Aborted: No target owner provided.")
        sys.exit(1)

    new_full_name = f"{target_owner}/{repo_name}"
    print(f"\nSummary:")
    print(f"  Current: {current_full_name}")
    print(f"  Target:  {new_full_name}")

    if not prompt_yes_no(f"Are you sure you want to transfer '{current_full_name}' to '{target_owner}'?", default=False):
        sys.exit("Aborted.")

    # Execute transfer
    print(f"\nInitiating transfer to '{target_owner}' via GitHub API...")
    
    # Using gh api to perform the transfer
    # POST /repos/{owner}/{repo}/transfer
    # Body: {"new_owner": "target_owner"}
    transfer_payload = {"new_owner": target_owner}
    import json
    
    transfer_cmd = [
        "gh", "api", 
        "-X", "POST", 
        f"repos/{current_owner}/{repo_name}/transfer",
        "--input", "-"
    ]
    
    # We'll use subprocess.run directly to pipe the json
    try:
        process = subprocess.run(
            transfer_cmd,
            input=json.dumps(transfer_payload),
            text=True,
            capture_output=True,
            check=False
        )
        
        if process.returncode == 0:
            print(f"\nSuccess! Repository transfer initiated.")
            print("Note: Transfers usually require the new owner to accept the invitation via email or GitHub notifications.")
            
            # Update local remote
            if prompt_yes_no("Would you like to update your local remote URL to point to the new owner?", default=True):
                if origin_url.startswith("git@"):
                    new_url = f"git@github.com:{target_owner}/{repo_name}.git"
                else:
                    new_url = f"https://github.com/{target_owner}/{repo_name}.git"
                
                print(f"Updating remote origin to: {new_url}")
                run_command(["git", "remote", "set-url", "origin", new_url])
                print("Remote URL updated.")
            
            # 5. Open in web
            web_url = f"https://github.com/{target_owner}/{repo_name}"
            if prompt_yes_no(f"Do you want to view the remote repository in your browser? ({web_url})", default=True):
                print(f"Opening {web_url}...")
                run_command(["gh", "repo", "view", "--web"], capture_output=False)
            else:
                print(f"Transfer initiated! Target: {web_url}")
        else:
            print(f"\nError: Transfer failed (Exit code {process.returncode})")
            print(f"Message: {process.stdout or process.stderr}")
    except Exception as e:
        print(f"\nError executing API request: {e}")

if __name__ == "__main__":
    main()

