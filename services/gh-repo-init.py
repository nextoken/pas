#!/usr/bin/env python3
"""
@pas-executable
Initialize a new GitHub repository from the current working folder.

This script:
1. Checks for GitHub CLI (gh) authentication.
2. Checks if the current folder is already a git repository.
3. Prompts to keep or replace existing git configuration.
4. Creates a new remote repository on GitHub.
5. Sets up the local git repository and pushes the initial state.
"""

import argparse
import os
import re
import subprocess
import sys
import shutil
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    get_git_info, 
    run_command, 
    prompt_yes_no, 
    check_gh_auth, 
    get_gh_protocol,
    format_menu_choices,
    prompt_toolkit_menu,
    console
)
from rich.panel import Panel

# --- Configuration ---
GH_LFS_THRESHOLD_MB = 50
GH_LFS_THRESHOLD_BYTES = GH_LFS_THRESHOLD_MB * 1024 * 1024
DEFAULT_VISIBILITY = "private"
# ---------------------

# --- URLs ---
GH_DOCS_URL = "https://docs.github.com/en/get-started/getting-started-with-git/about-remote-repositories"
# ------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    info_text = """
[bold]GitHub Repository Initialization[/bold]

Quickly publish local folders to GitHub:
• [cyan]Remote Creation[/cyan]: Creates a new repo (private or public) via gh CLI.
• [cyan]Git Setup[/cyan]: Initializes git locally, configures remotes and protocol.
• [cyan]LFS Support[/cyan]: Automatically scans for and tracks large files.
• [cyan]Clean Start[/cyan]: Optional removal of existing git config for a fresh push.
"""
    console.print(Panel(info_text.strip(), title="gh-repo-init", border_style="blue"))

def setup_lfs():
    """Scan for large files and set up Git LFS if needed."""
    console.print(f"\nScanning for large files (>{GH_LFS_THRESHOLD_MB}MB)...")
    large_files = []
    extensions = set()
    
    for root, dirs, files in os.walk("."):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            path = Path(root) / f
            try:
                if path.is_file() and path.stat().st_size > GH_LFS_THRESHOLD_BYTES:
                    large_files.append(path)
                    if path.suffix:
                        extensions.add(f"*{path.suffix}")
            except (FileNotFoundError, PermissionError):
                continue

    if not large_files:
        return True

    console.print(f"Found {len(large_files)} large files:")
    for f in large_files[:5]:
        size_mb = f.stat().st_size / (1024 * 1024)
        console.print(f"  - {f} ({size_mb:.1f} MB)")
    if len(large_files) > 5:
        console.print(f"  ... and {len(large_files) - 5} more.")

    if prompt_yes_no("\nWould you like to use Git LFS to track these files?", default=True):
        console.print("Setting up Git LFS...")
        run_command(["git", "lfs", "install"], capture_output=False)
        
        # Track found extensions
        for ext in sorted(extensions):
            console.print(f"Tracking {ext} via LFS...")
            run_command(["git", "lfs", "track", ext], capture_output=False)
        
        # Add .gitattributes
        run_command(["git", "add", ".gitattributes"], capture_output=False)
        return True
    
    return False

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
    args = parser.parse_args()

    show_summary()

    if not check_gh_auth():
        console.print("\n[bold red]Error:[/bold red] You are not authenticated with GitHub CLI.")
        console.print("Please run [cyan]gh auth login[/cyan] first.")
        sys.exit(1)
        
    gh_protocol = get_gh_protocol()
    
    # Fetch owners early
    owners = get_possible_owners()
    selected_owner = owners[0]

    git_info = get_git_info()
    git_dir = Path(".git")
    
    if git_info or git_dir.exists():
        console.print(f"\n[cyan]Current folder is already a git repository.[/cyan]")
        if git_info and git_info['origin']:
            console.print(f"  Remote origin: [bold]{git_info['origin']}[/bold]")
            
        if prompt_yes_no("Do you want to KEEP the existing git configuration?", default=True):
            if git_info and git_info['origin']:
                if prompt_yes_no("Would you like to RETRY the push to the existing remote?", default=True):
                    console.print("\n[bold green]Retrying push to existing remote...[/bold green]")
                    # Try to push. If it fails, the downstream error handling for large files etc. will kick in.
                    push_res = run_command(["git", "push", "-u", "origin", "main"], capture_output=False)
                    if push_res.returncode == 0:
                        console.print("\n[bold green]Success![/bold green] Repository is now synchronized.")
                        sys.exit(0)
                    else:
                        console.print("\n[bold yellow][!][/bold yellow] Push failed. Proceeding to troubleshooting steps...")
                        # We don't exit here; we'll let the standard creation flow handle the "repo already exists" scenario
                        # but we need to make sure selected_owner and repo_name are set.
            else:
                console.print("[green]Keeping local history. Proceeding to remote creation...[/green]")
                # Just continue the main flow
        else:
            if prompt_yes_no("Confirm: This will DELETE the local .git folder and start fresh?", default=False):
                console.print("Removing existing .git directory...")
                try:
                    if git_dir.is_dir():
                        run_command(["rm", "-rf", str(git_dir)], capture_output=False)
                        if git_dir.exists():
                            shutil.rmtree(git_dir)
                    elif git_dir.is_file():
                        git_dir.unlink()
                    console.print("Existing .git removed.")
                except Exception as e:
                    console.print(f"[bold yellow]Warning:[/bold yellow] Could not completely remove .git folder: {e}")
                    console.print("You may need to manually run: [cyan]rm -rf .git[/cyan]")
            else:
                sys.exit("Aborted.")

    # Fetch owners early
    owners = get_possible_owners()
    selected_owner = owners[0]
    folder_name = Path.cwd().name
    repo_name = folder_name

    # Try to extract defaults from existing origin if available
    if git_info and git_info['origin']:
        # Match git@github.com:owner/repo.git or https://github.com/owner/repo.git
        origin_url = git_info['origin']
        match = re.search(r'github\.com[:/]([^/]+)/([^.]+)(\.git)?', origin_url)
        if match:
            selected_owner = match.group(1)
            repo_name = match.group(2)

    # Select Owner first if there are options
    if len(owners) > 1:
        # If we have a selected_owner from origin, make it the default or ensure it's in the list
        default_idx = 0
        if selected_owner in owners:
            default_idx = owners.index(selected_owner)
            
        menu_items = [{"title": o, "value": o} for o in owners]
        menu_items.append({"title": "[Quit]", "value": "quit"})
        
        formatted_choices = format_menu_choices(menu_items, title_field="title", value_field="value")
        console.print("\n[bold cyan]Select Owner (User/Org):[/bold cyan]")
        selected_owner = prompt_toolkit_menu(formatted_choices, default_idx=default_idx)
        
        if not selected_owner or selected_owner == "quit":
            sys.exit("Aborted.")

    # Gather info for new repo
    repo_name_input = input(f"\nEnter repository name [{selected_owner}/{repo_name}]: ").strip()
    if repo_name_input:
        repo_name = repo_name_input
    
    # Visibility menu
    vis_items = [
        {"title": "Private", "value": True},
        {"title": "Public", "value": False},
        {"title": "[Quit]", "value": "quit"}
    ]
    formatted_vis = format_menu_choices(vis_items, title_field="title", value_field="value")
    console.print("\n[bold cyan]Select Visibility:[/bold cyan]")
    is_private = prompt_toolkit_menu(formatted_vis)
    if is_private == "quit": sys.exit("Aborted.")
    if is_private is None: is_private = True # Default to Private

    # Protocol menu
    proto_items = [
        {"title": f"SSH (git@github.com:...) [gh default: {gh_protocol}]", "value": "ssh"},
        {"title": "HTTPS (https://github.com/...)", "value": "https"},
        {"title": "[Quit]", "value": "quit"}
    ]
    formatted_proto = format_menu_choices(proto_items, title_field="title", value_field="value")
    console.print("\n[bold cyan]Select Protocol:[/bold cyan]")
    proto_choice = prompt_toolkit_menu(formatted_proto)
    if proto_choice == "quit": sys.exit("Aborted.")
    use_ssh = proto_choice != "https"

    console.print(f"\n[bold cyan]Summary:[/bold cyan]")
    console.print(f"  Owner:      [green]{selected_owner}[/green]")
    console.print(f"  Name:       [green]{repo_name}[/green]")
    console.print(f"  Visibility: [green]{'Private' if is_private else 'Public'}[/green]")
    console.print(f"  Protocol:   [green]{'SSH' if use_ssh else 'HTTPS'}[/green]")
    
    if not prompt_yes_no("Proceed with creation?", default=True):
        sys.exit("Aborted.")

    # 1. Initialize git locally
    console.print("\nInitializing local git repository...")
    run_command(["git", "init"], capture_output=False)
    
    # NEW: Check for LFS before adding files
    setup_lfs()
    
    # 2. Add files and initial commit
    console.print("Adding files and creating initial commit...")
    run_command(["git", "add", "."], capture_output=False)
    
    # Check if there is anything to commit
    status_res = run_command(["git", "status", "--porcelain"])
    if not status_res.stdout.strip():
        console.print("\n[bold yellow][!][/bold yellow] Nothing to commit. The folder appears to be empty or all files are ignored.")
        if prompt_yes_no("Would you like to create a placeholder README.md to proceed?", default=True):
            readme_content = f"# {repo_name}\n\nInitialized via PAS `gh-repo-init`."
            Path("README.md").write_text(readme_content)
            run_command(["git", "add", "README.md"], capture_output=False)
        else:
            console.print("[bold red]Aborted:[/bold red] Cannot initialize a repository without at least one commit.")
            sys.exit(1)

    commit_res = run_command(["git", "commit", "-m", "Initial commit"], capture_output=False)
    if commit_res.returncode != 0:
        console.print("[bold red]Error:[/bold red] Failed to create initial commit.")
        sys.exit(1)

    # 3. Create remote repo via gh
    full_repo_path = f"{selected_owner}/{repo_name}"
    console.print(f"Creating remote repository '[bold cyan]{full_repo_path}[/bold cyan]' on GitHub...")
    visibility_flag = "--private" if is_private else "--public"
    create_cmd = [
        "gh", "repo", "create", full_repo_path,
        "--source=.",
        "--push",
        visibility_flag
    ]
    
    # Determine the protocol to set after creation
    result = run_command(create_cmd, capture_output=False)
    
    if result.returncode != 0:
        # First check if it failed due to large files (which happens during the --push)
        repo_view = run_command(["gh", "repo", "view", full_repo_path])
        if repo_view.returncode == 0:
            # Repo was created, but push failed (likely LFS)
            console.print("\n[bold yellow][!][/bold yellow] Repository created, but initial push failed.")
            if prompt_yes_no("This often happens if large files (>100MB) are not tracked by LFS. Try setting up LFS and retrying?", default=True):
                setup_lfs()
                run_command(["git", "add", "."], capture_output=False)
                run_command(["git", "commit", "--amend", "--no-edit"], capture_output=False)
                console.print("Retrying push...")
                result = run_command(["git", "push", "-u", "origin", "main"], capture_output=False)
                if result.returncode != 0:
                    console.print("\n[bold red]Error:[/bold red] Retry push failed.")
                    sys.exit(1)
        
        # If still failing, check if error is because repo already exists
        if result.returncode != 0:
            check_exists = run_command(["gh", "repo", "view", full_repo_path])
            if check_exists.returncode == 0:
                console.print(f"\n[bold yellow][!][/bold yellow] Repository '{full_repo_path}' already exists on GitHub.")
                if prompt_yes_no("Do you want to initialize locally and OVERWRITE the remote history?", default=False):
                    console.print("\nProceeding with force-push to existing repository...")
                    if use_ssh:
                        remote_url = f"git@github.com:{selected_owner}/{repo_name}.git"
                    else:
                        remote_url = f"https://github.com/{selected_owner}/{repo_name}.git"
                    
                    add_remote = run_command(["git", "remote", "add", "origin", remote_url])
                    if add_remote.returncode != 0:
                        run_command(["git", "remote", "set-url", "origin", remote_url])
                    
                    console.print(f"Pushing local state to [cyan]{remote_url}[/cyan]...")
                    push_res = run_command(["git", "push", "-u", "origin", "main", "--force"], capture_output=False)
                    
                    if push_res.returncode != 0:
                        console.print("\n[bold yellow][!][/bold yellow] Push failed. Checking for large file errors...")
                        if prompt_yes_no("Push failed. This often happens if large files (>100MB) are not tracked by LFS. Try setting up LFS and retrying?", default=True):
                            setup_lfs()
                            run_command(["git", "add", "."], capture_output=False)
                            run_command(["git", "commit", "--amend", "--no-edit"], capture_output=False)
                            console.print("Retrying push...")
                            push_res = run_command(["git", "push", "-u", "origin", "main", "--force"], capture_output=False)

                    if push_res.returncode == 0:
                        console.print("\n[bold green]Success![/bold green] Repository has been synchronized (overwritten).")
                        sys.exit(0)
                    else:
                        console.print("\n[bold red]Error:[/bold red] Failed to push to existing repository.")
                        sys.exit(1)
                else:
                    console.print("\nAborted. No changes made to remote.")
                    sys.exit(1)
            else:
                console.print("\n[bold red]Error:[/bold red] Failed to create repository via 'gh'.")
                sys.exit(1)

    # 4. Correct protocol if needed (only if created fresh)
    try:
        if use_ssh:
            remote_url = f"git@github.com:{selected_owner}/{repo_name}.git"
        else:
            remote_url = f"https://github.com/{selected_owner}/{repo_name}.git"
            
        console.print(f"Setting remote 'origin' to: [cyan]{remote_url}[/cyan]")
        run_command(["git", "remote", "set-url", "origin", remote_url])
    except Exception as e:
        console.print(f"[bold yellow]Warning:[/bold yellow] Could not automatically set preferred protocol: {e}")

    console.print("\n[bold green]Success![/bold green] Repository has been created and initialized.")
    
    web_url = f"https://github.com/{selected_owner}/{repo_name}"
    if prompt_yes_no(f"Do you want to view the remote repository in your browser? ({web_url})", default=True):
        console.print(f"Opening {web_url}...")
        run_command(["gh", "repo", "view", "--web"], capture_output=False)
    else:
        run_command(["gh", "repo", "view"], capture_output=False)

if __name__ == "__main__":
    main()

