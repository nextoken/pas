#!/usr/bin/env python3
"""
@pas-executable
Set up GitHub Deploy Key pair for a remote server.
Use case: Grant a remote server READ access to a private repository via SSH.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel

from helpers.core import (
    get_git_info,
    check_gh_auth,
    get_ssh_keys,
    prompt_yes_no,
    run_command,
    load_pas_config,
    save_pas_config
)

console = Console()

def generate_deploy_key(key_path: Path, repo_name: str, host: str) -> bool:
    """Generate a new Ed25519 key pair for deployment."""
    print(f"\nGenerating new deploy key: {key_path}")
    cmd = [
        "ssh-keygen", "-t", "ed25519",
        "-f", str(key_path),
        "-N", "", # No passphrase
        "-C", f"deploy-key-{repo_name}-on-{host}"
    ]
    res = run_command(cmd, capture_output=False)
    return res.returncode == 0

def add_to_github(repo_path: str, pub_key_path: Path, title: str) -> bool:
    """Add the public key as a deploy key to the GitHub repository."""
    print(f"Adding deploy key to GitHub: {repo_path}...")
    # --read-only is the default for deploy keys, which is what we want
    cmd = [
        "gh", "repo", "deploy-key", "add", str(pub_key_path),
        "--repo", repo_path,
        "--title", title
    ]
    res = run_command(cmd, capture_output=True)
    if res.returncode == 0:
        return True
    
    if "key is already in use" in res.stderr:
        print(f"\n[!] Note: This key is already registered on GitHub (likely for this or another repository).")
        if prompt_yes_no("Proceed to distribute the private key to the remote server anyway?", default=True):
            return True
            
    print(f"Error: Failed to add deploy key to GitHub: {res.stderr}")
    return False

def main():
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", help="GitHub repository in 'owner/repo' format")
    args = parser.parse_args()

    info_text = """
[bold]GitHub Deploy Key Setup[/bold]

Grants a remote server access to a private repository:
- [cyan]Key Generation[/cyan]: Creates a dedicated Ed25519 deploy key.
- [cyan]GitHub Registration[/cyan]: Adds the public key to your repository via CLI.
- [cyan]Secure Distribution[/cyan]: Automatically installs the private key on your server.
- [cyan]Multi-Server[/cyan]: Use labels to manage access for multiple environments.
"""
    console.print(Panel(info_text.strip(), title="gh-repo-access-grant", border_style="blue"))
    console.print("\n")

    # 1. Initial Checks
    if not check_gh_auth():
        print("Error: Not authenticated with GitHub CLI. Please run 'gh auth login'.")
        sys.exit(1)

    git_info = get_git_info()
    repo_path = args.repo or ""
    repo_url = ""

    if not repo_path and git_info:
        repo_url = git_info['origin']
        # Extract owner/repo
        match = re.search(r"github\.com[:/]([^/]+/[^/\s]+)", repo_url)
        if match:
            repo_path = match.group(1).replace(".git", "")
            print(f"Detected GitHub repository: {repo_path}")
        else:
            print(f"Warning: Could not determine GitHub repository path from origin URL: {repo_url}")

    if not repo_path:
        repo_path = input("Enter GitHub repository (owner/repo): ").strip()
        if not repo_path:
            print("Error: GitHub repository is required.")
            sys.exit(1)
        # If we didn't have a repo_url from git_info, construct a default one for display/next steps
        if not repo_url:
            repo_url = f"git@github.com:{repo_path}.git"

    repo_name = repo_path.split("/")[-1]

    print(f"\nSetting up Repository Access for: {repo_path}")

    # 2. Gather Server Info (Reuse config from 'gh' used by gh-actions-secrets-vars-setup)
    config = load_pas_config("gh")
    projects = config.get("projects", {})
    project_config = projects.get(repo_path, {})
    global_config = config.get("global", {})

    default_host = project_config.get("remote_host") or global_config.get("remote_host") or ""
    default_user = project_config.get("remote_user") or global_config.get("remote_user") or ""

    remote_host_input = input(f"Enter REMOTE_HOST (server that needs access) [{default_host}]: ").strip() or default_host
    
    remote_host = remote_host_input
    remote_user = default_user

    # Support user@host format
    if "@" in remote_host_input:
        remote_user, remote_host = remote_host_input.split("@", 1)
    else:
        remote_user = input(f"Enter REMOTE_USER (username on server) [{default_user}]: ").strip() or default_user

    if not remote_host or not remote_user:
        print("Error: REMOTE_HOST and REMOTE_USER are required.")
        sys.exit(1)

    # 3. Server Label for Naming
    # Use the first part of the hostname or the full host if IP
    default_label = remote_host.split('.')[0]
    server_label = input(f"Enter a label for this server (for key naming) [{default_label}]: ").strip() or default_label

    # 4. Handle Key Generation/Selection
    ssh_dir = Path.home() / ".ssh"
    key_name = f"id_ed25519_deploy_{server_label}_{repo_name}"
    key_path = ssh_dir / key_name
    pub_key_path = key_path.with_suffix(".pub")

    if key_path.exists():
        print(f"\n[!] Key already exists: {key_path}")
        if prompt_yes_no("Use existing key?", default=True):
            pass # Continue with existing key
        elif prompt_yes_no("Overwrite existing key?", default=False):
            if not generate_deploy_key(key_path, repo_name, remote_host):
                print("Error: Failed to generate key pair.")
                sys.exit(1)
        else:
            print("Aborted. Please provide a different label to use a different key name.")
            sys.exit(0)
    else:
        if not generate_deploy_key(key_path, repo_name, remote_host):
            print("Error: Failed to generate key pair.")
            sys.exit(1)

    # 4. Add to GitHub
    title = f"Deploy Key ({remote_user}@{remote_host})"
    if not add_to_github(repo_path, pub_key_path, title):
        print("Error: Failed to add deploy key to GitHub.")
        sys.exit(1)

    # 5. Distribute to Remote Server
    print("\n--- Distributing Private Key to Remote Server ---")
    remote_ssh_dir = "~/.ssh"
    remote_key_path = f"{remote_ssh_dir}/{key_name}"
    
    # Ensure remote .ssh exists and has correct permissions
    # Use xssh for smart detection and profile support
    import shlex
    # Use --tty to allow interactive key selection if the profile fails
    ensure_ssh_dir = f"xssh {remote_user}@{remote_host} --tty 'mkdir -p {remote_ssh_dir} && chmod 700 {remote_ssh_dir}'"
    print(f"Ensuring {remote_ssh_dir} exists on {remote_host}...")
    run_command(shlex.split(ensure_ssh_dir), capture_output=False)

    # Copy private key using xscp
    # xscp will use the profile if it exists
    scp_cmd = f"xscp {key_path} {remote_user}@{remote_host}:{remote_key_path}"
    print(f"Copying private key to {remote_host}...")
    if run_command(shlex.split(scp_cmd), capture_output=False).returncode == 0:
        # Set permissions on remote key
        chmod_cmd = f"xssh {remote_user}@{remote_host} --tty 'chmod 600 {remote_key_path}'"
        run_command(shlex.split(chmod_cmd), capture_output=False)
        print(f"\n[✓] Private key successfully installed on {remote_host} at {remote_key_path}")
        
        print("\n" + "="*60)
        print("NEXT STEPS ON THE SERVER")
        print("="*60)
        print(f"If PAS is installed on the remote server, you can clone using:")
        print(f"\n  git-use-key {repo_url}")
        print(f"\nSelect the key: {key_name}")
        print("\n---")
        print("OR MANUALLY (if PAS is not installed):")
        print(f"Add this to your {remote_user}@{remote_host} ~/.ssh/config:")
        print(f"\nHost github.com-{repo_name}")
        print(f"    HostName github.com")
        print(f"    User git")
        print(f"    IdentityFile {remote_key_path}")
        print(f"    IdentitiesOnly yes")
        print(f"\nThen clone using:")
        print(f"git clone git@github.com-{repo_name}:{repo_path}.git")
        print("="*60)
    else:
        print("\nError: Failed to copy private key to remote server.")

if __name__ == "__main__":
    main()

