#!/usr/bin/env python3
"""
@pas-executable
Set up GitHub Actions Secrets, environment variables, and distribute SSH keys for deployment.
Use case: One-time configuration of GitHub Actions and server Handshake for CI/CD pipelines.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel

from helpers.core import (
    load_pas_config, 
    save_pas_config, 
    prompt_yes_no, 
    get_ssh_keys, 
    get_git_info, 
    check_gh_auth,
    run_command
)

console = Console()

def generate_automation_key(key_path: Path) -> bool:
    """Generate a new Ed25519 key pair with no passphrase."""
    print(f"Generating new automation key: {key_path}")
    cmd = [
        "ssh-keygen", "-t", "ed25519",
        "-f", str(key_path),
        "-N", "", # No passphrase
        "-C", f"gh-actions-deploy-{get_git_info()['root'].split('/')[-1]}"
    ]
    res = run_command(cmd, capture_output=False)
    return res.returncode == 0

def select_or_create_key(default_key_name: Optional[str] = None) -> Optional[Path]:
    """Interactively select an existing key or create a new one."""
    keys = get_ssh_keys()
    
    print("\n--- SSH Key for Deployment ---")
    default_idx = 1
    if keys:
        for idx, key in enumerate(keys, 1):
            is_default = " [default]" if default_key_name and key.name == default_key_name else ""
            if is_default:
                default_idx = idx
            print(f"{idx}. Use existing: {key.name}{is_default}")
        print(f"{len(keys) + 1}. Create a NEW dedicated automation key")
    else:
        print("No existing keys found. You'll need to create one.")
        print("1. Create a NEW dedicated automation key")

    print("q. Quit")
    
    choice = input(f"\nSelect an option [{default_idx if keys else 1}]: ").strip().lower()
    if choice == 'q':
        return None
    if not choice:
        choice = str(default_idx if keys else 1)
        
    try:
        sel = int(choice)
        if keys and 1 <= sel <= len(keys):
            return keys[sel-1]
        elif sel == (len(keys) + 1 if keys else 1):
            # Create new
            ssh_dir = Path.home() / ".ssh"
            ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            new_key_name = f"id_ed25519_pas_deploy_{Path.cwd().name}"
            new_key_path = ssh_dir / new_key_name
            
            if new_key_path.exists():
                if not prompt_yes_no(f"Key {new_key_name} already exists. Overwrite?", default=False):
                    return select_or_create_key(default_key_name)
            
            if generate_automation_key(new_key_path):
                return new_key_path
        else:
            print("Invalid selection.")
            return select_or_create_key(default_key_name)
    except ValueError:
        print("Please enter a number.")
        return select_or_create_key(default_key_name)
    
    return None

def test_remote_access(host: str, user: str, key_path: Path):
    """Test SSH access to the remote host."""
    print(f"\nTesting access to {user}@{host}...")
    # Use str(key_path.absolute()) to ensure absolute path
    cmd = ["ssh", "-i", str(key_path.absolute()), "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"{user}@{host}", "echo 'SSH Connection: OK'"]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n[✓] Remote host is accessible!")
    else:
        print("\n[!] Failed to connect to remote host.")

def set_gh_secret(name: str, value: str = None, file_path: Path = None):
    """Set a GitHub secret (encrypted)."""
    if file_path:
        print(f"Uploading secret '{name}' from file...")
        cmd = f"gh secret set {name} < {file_path}"
        res = subprocess.run(cmd, shell=True)
        return res.returncode == 0
    else:
        print(f"Setting secret '{name}'...")
        res = run_command(["gh", "secret", "set", name, "--body", value])
        return res.returncode == 0

def set_gh_variable(name: str, value: str):
    """Set a GitHub variable (plain text configuration)."""
    print(f"Setting variable '{name}' to '{value}'...")
    res = run_command(["gh", "variable", "set", name, "--body", value])
    return res.returncode == 0

def parse_repo_path(url: str) -> Optional[str]:
    """Extract owner/repo from a GitHub URL."""
    if not url:
        return None
    # git@github.com:owner/repo.git or https://github.com/owner/repo.git
    match = re.search(r"github\.com[:/]([^/]+/[^/\s]+)", url)
    if match:
        repo = match.group(1).replace(".git", "")
        return repo
    return None

def main():
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    args = parser.parse_args()

    info_text = """
[bold]GitHub Actions CI/CD Setup[/bold]

One-time configuration for automated deployment:
- [cyan]Variables[/cyan]: Sets REMOTE_HOST, REMOTE_USER, and REMOTE_PATH on GitHub.
- [cyan]SSH Secrets[/cyan]: Securely uploads SSH_PRIVATE_KEY to GitHub Actions.
- [cyan]Handshake[/cyan]: Automatically authorizes the public key on your server.
- [cyan]Persistence[/cyan]: Remembers server details per project.
"""
    console.print(Panel(info_text.strip(), title="gh-actions-secrets-vars-setup", border_style="blue"))
    console.print("\n")

    # 1. Initial Checks
    git_info = get_git_info()
    if not git_info:
        print("Error: Current directory is not a git repository.")
        sys.exit(1)
        
    if not check_gh_auth():
        print("Error: Not authenticated with GitHub CLI. Please run 'gh auth login'.")
        sys.exit(1)

    print(f"\nSetting up secrets for: {git_info['root']}")
    repo_path = parse_repo_path(git_info['origin'])
    if not repo_path:
        print("Warning: Could not determine GitHub repository path from origin URL.")
        repo_path = "default"

    # 2. Gather Server Info (with persistence)
    config = load_pas_config("gh")
    
    # Structure: { "projects": { "owner/repo": { ... } }, "global": { ... } }
    projects = config.get("projects", {})
    project_config = projects.get(repo_path, {})
    global_config = config.get("global", {})

    # Use project-specific, then global, then hardcoded default
    default_host = project_config.get("remote_host") or global_config.get("remote_host") or "pas.example.com"
    default_user = project_config.get("remote_user") or global_config.get("remote_user") or ""
    default_path = project_config.get("remote_path") or ""
    default_key_name = project_config.get("ssh_key_name")

    remote_host = input(f"Enter REMOTE_HOST [{default_host}]: ").strip() or default_host
    remote_user = input(f"Enter REMOTE_USER (username on server) [{default_user}]: ").strip() or default_user
    
    if not remote_user:
        print("Error: REMOTE_USER is required.")
        sys.exit(1)
        
    remote_path = input(f"Enter REMOTE_PATH (e.g. /home/user/apps/project) [{default_path}]: ").strip() or default_path
    if not remote_path:
        print("Error: REMOTE_PATH is required.")
        sys.exit(1)

    # 3. Handle GitHub Configuration Updates
    print("\n--- GitHub Configuration ---")
    
    # 3a. Variables (Host, User, Path) - Always offer to update if we have the info
    if prompt_yes_no("Update REMOTE_* variables on GitHub?", default=True):
        print("Uploading variables...")
        success = True
        success &= set_gh_variable("REMOTE_HOST", value=remote_host)
        success &= set_gh_variable("REMOTE_USER", value=remote_user)
        success &= set_gh_variable("REMOTE_PATH", value=remote_path)
        if success:
            print("Variables updated successfully.")
        else:
            print("Some variables failed to update.")

    # 3b. SSH Key Secret
    selected_key = None
    if prompt_yes_no("Update SSH_PRIVATE_KEY secret on GitHub?", default=False):
        selected_key = select_or_create_key(default_key_name)
        if selected_key:
            if set_gh_secret("SSH_PRIVATE_KEY", file_path=selected_key):
                print("SSH_PRIVATE_KEY updated successfully.")
            else:
                print("Failed to update SSH_PRIVATE_KEY.")
        else:
            print("No key selected, skipping secret update.")

    # Save to project specific and update global defaults
    project_config = {
        "remote_host": remote_host,
        "remote_user": remote_user,
        "remote_path": remote_path,
        "ssh_key_name": selected_key.name if selected_key else default_key_name
    }
    projects[repo_path] = project_config
    
    config["projects"] = projects
    config["global"] = {
        "remote_host": remote_host,
        "remote_user": remote_user
    }
    save_pas_config("gh", config)

    # 4. Handshake Guide (Only if a key was selected/updated)
    if selected_key:
        pub_key_path = selected_key.with_suffix(".pub")
        if not pub_key_path.exists():
            print(f"Warning: Public key not found at {pub_key_path}")
            sys.exit(0)
            
        pub_key_content = pub_key_path.read_text().strip()
        
        print("\n" + "="*60)
        print("FINAL STEP: Authorize Public Key on Remote Server")
        print("="*60)
        print("GitHub Actions now has the PRIVATE key, but your SERVER must trust the PUBLIC key.")
        
        remote_tmp_path = f"~/deploy_key_{Path.cwd().name}.pub"
        
        scp_cmd = f"scp {pub_key_path} {remote_user}@{remote_host}:{remote_tmp_path}"
        ssh_cmd = (
            f"ssh {remote_user}@{remote_host} "
            f"\"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"cat {remote_tmp_path} >> ~/.ssh/authorized_keys && "
            f"chmod 600 ~/.ssh/authorized_keys && "
            f"rm {remote_tmp_path}\""
        )
        
        print("\nTo authorize the key, these commands will be run:")
        print(f"1. Copy key:   {scp_cmd}")
        print(f"2. Update auth: {ssh_cmd}")
        
        if prompt_yes_no("\nWould you like me to run these commands for you now?", default=True):
            print(f"Copying key to {remote_host}...")
            scp_res = subprocess.run(scp_cmd, shell=True)
            if scp_res.returncode == 0:
                print("Updating authorized_keys...")
                ssh_res = subprocess.run(ssh_cmd, shell=True)
                if ssh_res.returncode == 0:
                    print("\nSUCCESS! Your server is now ready for GitHub Action deployments.")
                else:
                    print("\nError: Failed to update authorized_keys via SSH.")
            else:
                print("\nError: Failed to copy public key via SCP.")

    # 5. Final Accessibility Test
    if prompt_yes_no("\nWould you like to test SSH accessibility to the remote host now?", default=True):
        test_key = selected_key
        if not test_key:
            # If we didn't just update the key, ask which one to use for the test
            print("\nSelect a key to use for the connectivity test:")
            test_key = select_or_create_key(project_config.get("ssh_key_name"))
        
        if test_key:
            test_remote_access(remote_host, remote_user, test_key)
        else:
            print("No key selected for testing.")

if __name__ == "__main__":
    main()

