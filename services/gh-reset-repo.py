#!/usr/bin/env python3
"""
@pas-executable
Automate the deletion and reset (recreation) of a GitHub repository history.

This script guides the user through the process of:
1. Detecting the current repository configuration (owner, name, visibility).
2. Verifying GitHub CLI (gh) authentication.
3. Deleting the remote repository on GitHub (destructive action).
4. Initializing a new repository with the current local state.

Prerequisites:
- GitHub CLI (gh) installed and authenticated.
- A local git repository with an 'origin' remote pointing to GitHub.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import json
from typing import NamedTuple, Optional

from rich.console import Console
from rich.panel import Panel

from helpers.core import prompt_yes_no

console = Console()

class RepoInfo(NamedTuple):
    owner: str
    name: str
    is_private: bool
    use_ssh: bool


def run_command(cmd: list[str], capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        return e


def get_repo_details() -> RepoInfo:
    """Extract owner and name from git remote and detect visibility."""
    # 1. Get remote URL
    result = run_command(["git", "remote", "get-url", "origin"])
    if result.returncode != 0:
        sys.exit("Error: Could not find 'origin' remote. Is this a git repository?")
    
    url = result.stdout.strip()
    use_ssh = url.startswith("git@") or "ssh://" in url
    
    # Matches:
    # git@github.com:owner/repo.git
    # https://github.com/owner/repo.git
    match = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)(?:\.git)?", url)
    if not match:
        sys.exit(f"Error: Could not parse GitHub owner and name from URL: {url}")
    
    owner, name = match.groups()
    
    # 2. Detect visibility via gh
    print(f"Detecting visibility for {owner}/{name}...")
    result = run_command(["gh", "repo", "view", f"{owner}/{name}", "--json", "isPrivate"])
    
    is_private = True  # Default to private for safety
    if result.returncode == 0:
        import json
        try:
            data = json.loads(result.stdout)
            is_private = data.get("isPrivate", True)
            print(f"Detected visibility: {'Private' if is_private else 'Public'}")
        except json.JSONDecodeError:
            print("Warning: Could not parse visibility. Defaulting to Private.")
    else:
        print("Warning: Could not reach GitHub API to detect visibility. Defaulting to Private.")
        
    return RepoInfo(owner=owner, name=name, is_private=is_private, use_ssh=use_ssh)


def check_auth() -> str:
    """Verify gh CLI authentication and return default git protocol."""
    print("Checking GitHub CLI authentication...")
    result = run_command(["gh", "auth", "status"], capture_output=False)
    if result.returncode != 0:
        print("\nError: You are not authenticated with GitHub CLI.")
        print("Please run 'gh auth login' first.")
        sys.exit(1)
    
    # Check default protocol
    protocol_result = run_command(["gh", "config", "get", "git_protocol"])
    return protocol_result.stdout.strip() or "https"


def prompt_confirm(message: str) -> bool:
    """Prompt user for confirmation."""
    # Deprecated: use prompt_yes_no from helpers
    return prompt_yes_no(message, default=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ssh", action="store_true", help="Force use of SSH for origin remote")
    parser.add_argument("--https", action="store_true", help="Force use of HTTPS for origin remote")
    args = parser.parse_args()

    info_text = """
[bold red]GitHub Repository History Reset[/bold red]

Destructively wipes history and restarts from current local files:
- [cyan]Remote Deletion[/cyan]: Deletes the existing repo on GitHub.
- [cyan]Local Backup[/cyan]: Backs up your current .git folder to .git.bak.
- [cyan]Fresh Start[/cyan]: Re-initializes git, creates a new "Initial commit".
- [cyan]Re-publish[/cyan]: Re-creates the remote repo and pushes the new state.
"""
    console.print(Panel(info_text.strip(), title="gh-reset-repo", border_style="red"))
    console.print("\n")

    gh_protocol = check_auth()
    if gh_protocol == "https":
        print("\nNOTE: Your GitHub CLI (gh) is currently configured to use HTTPS by default.")
        print("To change it to SSH permanently, run: gh config set git_protocol ssh")
    
    repo = get_repo_details()
    
    # Override SSH preference if flags are provided
    use_ssh = repo.use_ssh
    if args.ssh:
        use_ssh = True
    elif args.https:
        use_ssh = False
        
    full_name = f"{repo.owner}/{repo.name}"
    
    print(f"\nTarget Repository: {full_name}")
    print(f"Visibility: {'Private' if repo.is_private else 'Public'}")
    print(f"Original Protocol: {'SSH' if repo.use_ssh else 'HTTPS'}")
    if use_ssh != repo.use_ssh:
        print(f"Target Protocol: {'SSH' if use_ssh else 'HTTPS'} (Overridden)")
    else:
        print(f"Target Protocol: {'SSH' if use_ssh else 'HTTPS'}")
    
    print("\nWARNING: This will DESTRUCTIVELY DELETE the repository on GitHub AND the local git history.")
    print("All previous commits, issues, pull requests, and wiki content will be lost.")
    print("A new 'Initial commit' will be created from your current files.")
    
    # Require typing the full repo name to confirm deletion
    print(f"\nTo confirm DELETION OF ALL HISTORY, please type the full repository name: {full_name}")
    user_input = input("Enter full repository name: ").strip()
    
    if user_input != full_name:
        sys.exit(f"Aborted: Input '{user_input}' does not match '{full_name}'.")
    
    if not prompt_yes_no(f"Are you absolutely sure you want to WIPE ALL HISTORY and recreate {full_name}?", default=False):
        sys.exit("Aborted.")

    # 1. Delete remote
    print(f"\nDeleting repository {full_name} from GitHub...")
    delete_cmd = ["gh", "repo", "delete", full_name, "--yes"]
    result = run_command(delete_cmd, capture_output=False)
    if result.returncode != 0:
        sys.exit("Failed to delete repository. Aborting.")

    # 2. Backup and wipe local history
    print("\nBacking up and wiping local git history...")
    git_dir = Path(".git")
    backup_git = Path(".git.bak")
    
    if git_dir.exists():
        if backup_git.exists():
            import shutil
            shutil.rmtree(backup_git)
        git_dir.rename(backup_git)
        print(f"Old history backed up to {backup_git}")
    
    run_command(["git", "init"], capture_output=False)
    
    # Ensure .git.bak is ignored
    gitignore = Path(".gitignore")
    ignore_entry = ".git.bak\n"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".git.bak" not in content:
            print("Adding .git.bak to .gitignore...")
            with gitignore.open("a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(ignore_entry)
    else:
        print("Creating .gitignore with .git.bak...")
        gitignore.write_text(ignore_entry)

    run_command(["git", "add", "."], capture_output=False)
    run_command(["git", "commit", "-m", "Initial commit (post-cleanup)"], capture_output=False)

    # 3. Create and push
    print(f"\nRecreating repository {full_name} on GitHub...")
    visibility_flag = "--private" if repo.is_private else "--public"
    create_cmd = [
        "gh", "repo", "create", full_name,
        "--source=.",
        "--remote=origin",
        "--push",
        visibility_flag
    ]
    
    result = run_command(create_cmd, capture_output=False)
    if result.returncode != 0:
        print("\nFailed to recreate repository.")
        print("Your old history is preserved in .git.bak")
        print("You may need to manually run:")
        print(f"  gh repo create {repo.name} {visibility_flag} --source=. --remote=origin --push")
        sys.exit(1)

    # 4. Ensure desired protocol is used
    if use_ssh:
        ssh_url = f"git@github.com:{repo.owner}/{repo.name}.git"
        print(f"\nEnsuring remote 'origin' uses SSH: {ssh_url}")
        run_command(["git", "remote", "set-url", "origin", ssh_url])
    elif not use_ssh and repo.use_ssh:
        https_url = f"https://github.com/{repo.owner}/{repo.name}.git"
        print(f"\nEnsuring remote 'origin' uses HTTPS: {https_url}")
        run_command(["git", "remote", "set-url", "origin", https_url])

    print("\nSuccess! Repository has been reset and pushed.")
    print(f"Note: Your old history is still available in {backup_git} if needed.")
    print("Verification:")
    run_command(["gh", "repo", "view"], capture_output=False)


if __name__ == "__main__":
    main()

