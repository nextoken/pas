#!/usr/bin/env python3
"""
@pas-executable
Generate a new SSH key pair or use an existing key, and optionally distribute to remote servers.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

# Import helpers
sys.path.insert(0, str(Path(__file__).parent))
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import prompt_yes_no, run_command, copy_to_clipboard, get_ssh_keys, format_menu_choices, prompt_toolkit_menu

console = Console()

# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "ssh-ops"
TOOL_TITLE = "SSH Key Manager"
TOOL_SHORT_DESC = "Generate SSH keys or use existing ones; optionally distribute to remote servers."
TOOL_DESCRIPTION = "Generate a new SSH key pair or use an existing key, and optionally distribute to remote servers."

def main():
    parser = argparse.ArgumentParser(
        description=TOOL_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    args = parser.parse_args()

    info_text = """
[bold]SSH Key Manager[/bold]

%s

- [cyan]Generate New Key[/cyan]: Create a new Ed25519 key pair (Developer or Automation mode).
- [cyan]Use Existing Key[/cyan]: Select from your existing SSH keys.
- [cyan]Auto-Distribution[/cyan]: Optionally distributes the public key to a remote server for password-less login.
- [cyan]Convenience[/cyan]: Copy public key to clipboard and test connections.
""" % TOOL_DESCRIPTION
    console.print(Panel(info_text.strip(), title=TOOL_TITLE, border_style="blue"))
    console.print("\n")

    # 1. Choose action: Generate new or use existing
    action_choices = [
        {"title": "Generate a new SSH key pair", "value": "generate"},
        {"title": "Use an existing SSH key", "value": "existing"},
        {"title": "[Quit]", "value": "quit"}
    ]
    formatted_choices = format_menu_choices(action_choices, title_field="title", value_field="value")
    selected_action = prompt_toolkit_menu(formatted_choices)
    
    if not selected_action or selected_action == "quit":
        console.print("[yellow]Aborted.[/yellow]")
        return
    
    if selected_action == "existing":
        key_path = select_existing_key()
        if not key_path:
            console.print("[yellow]No key selected. Aborted.[/yellow]")
            return
        # Use the existing key
        use_existing_key(key_path)
        return
    
    # Continue with generation flow
    # 2. Choose Mode
    console.print("[bold cyan]Select Key Generation Mode:[/bold cyan]")
    mode_choices = [
        {"title": "Developer Mode (Secure, uses a passphrase, for local use)", "value": False},
        {"title": "Automation Mode (No passphrase, for CI/CD or scripts)", "value": True},
        {"title": "[Back]", "value": None},
        {"title": "[Quit]", "value": None}
    ]
    formatted_mode_choices = format_menu_choices(mode_choices, title_field="title", value_field="value")
    selected_mode = prompt_toolkit_menu(formatted_mode_choices)
    
    if selected_mode is None:
        console.print("[yellow]Aborted.[/yellow]")
        return
    
    is_automation = selected_mode
    
    # 3. Key Name
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    
    default_name = "id_ed25519_automation" if is_automation else "id_ed25519_dev"
    key_name = input(f"Enter key name [{default_name}]: ").strip() or default_name
    key_path = ssh_dir / key_name
    
    if key_path.exists():
        if not prompt_yes_no(f"Key '{key_name}' already exists. Overwrite?", default=False):
            sys.exit("Aborted.")

    # 4. Passphrase
    passphrase = ""
    if not is_automation:
        import getpass
        while True:
            p1 = getpass.getpass("Enter passphrase (leave empty for none): ")
            if not p1:
                if prompt_yes_no("Are you sure you want NO passphrase for Developer mode?", default=False):
                    break
                continue
            p2 = getpass.getpass("Confirm passphrase: ")
            if p1 == p2:
                passphrase = p1
                break
            print("Passphrases do not match. Try again.")

    # 5. Generate Key
    print(f"\nGenerating Ed25519 key at {key_path}...")
    gen_cmd = [
        "ssh-keygen", "-t", "ed25519",
        "-f", str(key_path),
        "-N", passphrase,
        "-C", f"{os.getlogin()}@{os.uname().nodename}"
    ]
    
    result = run_command(gen_cmd, capture_output=False)
    if result.returncode != 0:
        sys.exit("\nError: Failed to generate key.")

    # 6. macOS Keychain Integration
    if sys.platform == "darwin" and not is_automation and passphrase:
        if prompt_yes_no("Add this key to your macOS Keychain?", default=True):
            console.print("Adding to keychain...")
            # Use --apple-use-keychain on modern macOS
            run_command(["ssh-add", "--apple-use-keychain", str(key_path)], capture_output=False)

    # 7. Output Public Key
    pub_key_path = key_path.with_suffix(".pub")
    pub_key_content = pub_key_path.read_text().strip()
    
    console.print("\n" + "="*40)
    console.print("[bold green]SUCCESS: Key pair generated.[/bold green]")
    console.print(f"Private Key: {key_path}")
    console.print(f"Public Key:  {pub_key_path}")
    console.print("="*40)
    console.print("\n[bold]Public Key Content:[/bold]")
    console.print(pub_key_content)
    console.print("\n" + "="*40)

    # 8. Process the new key (copy to clipboard, distribute, etc.)
    process_key(key_path, pub_key_path, is_automation)


def select_existing_key() -> Optional[Path]:
    """Select an existing SSH key from ~/.ssh/."""
    console.print("\n[bold cyan]Select Existing SSH Key[/bold cyan]")
    
    keys = get_ssh_keys()
    if not keys:
        console.print("[yellow]No SSH keys found in ~/.ssh/[/yellow]")
        return None
    
    key_choices = []
    for key_path in keys:
        key_name = key_path.name
        # Try to get the public key to show more info
        pub_key_path = key_path.with_suffix(".pub")
        if pub_key_path.exists():
            try:
                pub_content = pub_key_path.read_text().strip()
                # Extract key type and comment if available
                parts = pub_content.split()
                key_type = parts[0] if len(parts) > 0 else "unknown"
                comment = parts[2] if len(parts) > 2 else ""
                title = f"{key_name} ({key_type})"
                if comment:
                    title += f" - {comment}"
            except Exception:
                title = key_name
        else:
            title = f"{key_name} (no public key found)"
        
        key_choices.append({"title": title, "value": key_path})
    
    key_choices.append({"title": "[Back]", "value": None})
    key_choices.append({"title": "[Quit]", "value": None})
    
    formatted_choices = format_menu_choices(key_choices, title_field="title", value_field="value")
    selected_key = prompt_toolkit_menu(formatted_choices)
    
    return selected_key


def use_existing_key(key_path: Path):
    """Use an existing SSH key for distribution."""
    pub_key_path = key_path.with_suffix(".pub")
    
    if not pub_key_path.exists():
        console.print(f"[red]Error: Public key not found at {pub_key_path}[/red]")
        console.print("[yellow]Trying to generate public key from private key...[/yellow]")
        
        # Try to extract public key
        result = run_command(["ssh-keygen", "-y", "-f", str(key_path)], capture_output=True)
        if result.returncode == 0:
            pub_key_path.write_text(result.stdout.strip() + "\n")
            console.print(f"[green]Generated public key at {pub_key_path}[/green]")
        else:
            console.print("[red]Failed to generate public key. You may need to provide the passphrase.[/red]")
            if prompt_yes_no("Try again with passphrase prompt?", default=True):
                # This will prompt for passphrase interactively
                result = run_command(["ssh-keygen", "-y", "-f", str(key_path)], capture_output=False)
                if result.returncode == 0:
                    # We can't capture the output in non-capture mode, so we'll need to read it
                    console.print("[yellow]Please manually copy the displayed public key.[/yellow]")
                    return
            return
    
    # Process the existing key (assume it's not automation mode)
    process_key(key_path, pub_key_path, is_automation=False)


def process_key(key_path: Path, pub_key_path: Path, is_automation: bool = False):
    """Common processing for both new and existing keys: display, copy, distribute."""
    pub_key_content = pub_key_path.read_text().strip()
    
    console.print("\n" + "="*40)
    console.print("[bold cyan]SSH Key Information[/bold cyan]")
    console.print(f"Private Key: {key_path}")
    console.print(f"Public Key:  {pub_key_path}")
    console.print("="*40)
    console.print("\n[bold]Public Key Content:[/bold]")
    console.print(pub_key_content)
    console.print("\n" + "="*40)

    # Copy to clipboard
    if copy_to_clipboard(pub_key_content):
        console.print("[green]Copied public key to clipboard![/green]")
    elif prompt_yes_no("Copy public key to clipboard?", default=True):
        # Fallback if copy_to_clipboard didn't work
        if sys.platform == "darwin":
            process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            process.communicate(pub_key_content.encode('utf-8'))
            console.print("[green]Copied to clipboard![/green]")

    # Distribute to remote server
    if prompt_yes_no("\nDistribute this public key to a remote server for password-less login?", default=False):
        distribute_key_to_server(pub_key_path, key_path)

    console.print("\n[bold]Next steps:[/bold]")
    console.print("1. Paste the public key into GitHub/GitLab SSH settings.")
    if not is_automation:
        console.print(f"2. Use 'git-use-key' in any repo to use this specific key.")


def test_connectivity_first(server: str, private_key_path: Path) -> bool:
    """Test if password-less SSH connection already works. Returns True if successful."""
    console.print(f"\n[cyan]Testing existing connectivity to {server}...[/cyan]")
    
    # Test with the specific key
    cmd = [
        "ssh", "-i", str(private_key_path),
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",  # Non-interactive, will fail if password needed
        "-o", "ConnectTimeout=5",  # Don't hang too long
        server,
        "echo 'Connection successful!'"
    ]
    
    result = run_command(cmd, capture_output=True)
    
    if result.returncode == 0:
        console.print("[bold green]✓ Password-less SSH connection already works![/bold green]")
        return True
    else:
        return False


def distribute_key_to_server(pub_key_path: Path, private_key_path: Path):
    """Distribute the public key to a remote server using ssh-copy-id."""
    console.print("\n[bold cyan]SSH Key Distribution[/bold cyan]")
    
    # Prompt for server details
    server_input = input("Enter remote server (user@hostname or hostname): ").strip()
    if not server_input:
        console.print("[yellow]No server specified. Skipping distribution.[/yellow]")
        return
    
    # Parse server input
    if "@" in server_input:
        user, hostname_input = server_input.split("@", 1)
    else:
        user = input(f"Enter username for {server_input} [default: {os.getlogin()}]: ").strip() or os.getlogin()
        hostname_input = server_input
    
    # Ask if this is a new hostname/IP for an existing setup
    console.print("\n[dim]Note: If the hostname/IP changed but you're using the same key, you don't need a new key.[/dim]")
    console.print("[dim]The same SSH key works - we'll just update your SSH config.[/dim]")
    
    # Allow specifying a different hostname alias vs actual connection target
    hostname_alias = input(f"Enter SSH config alias (or press Enter to use '{hostname_input}'): ").strip() or hostname_input
    actual_hostname = hostname_input  # The actual hostname/IP to connect to
    
    server = f"{user}@{actual_hostname}"
    
    # Test connectivity first
    if test_connectivity_first(server, private_key_path):
        console.print(f"[green]SSH access to {server} is already configured and working.[/green]")
        
        # Offer to configure SSH config for easier access
        if prompt_yes_no(f"Configure SSH config to allow 'ssh {hostname_alias}' (without user@)?", default=False):
            configure_ssh_config(hostname_alias, user, private_key_path, actual_hostname)
        return
    
    # Connectivity doesn't work, proceed with distribution
    console.print(f"[yellow]Password-less access not configured. Proceeding with key distribution...[/yellow]")
    
    # Check if ssh-copy-id is available
    ssh_copy_id = shutil.which("ssh-copy-id")
    if not ssh_copy_id:
        console.print("[yellow]ssh-copy-id not found. Using manual method...[/yellow]")
        manual_key_distribution(server, pub_key_path, private_key_path)
        return
    
    # Use ssh-copy-id with the specific key
    console.print(f"\n[cyan]Copying public key to {server}...[/cyan]")
    console.print("[dim]You may be prompted for the remote server password.[/dim]")
    
    # Build ssh-copy-id command
    # -i specifies the identity file (public key)
    # -f forces mode (don't check if key already exists)
    cmd = [ssh_copy_id, "-i", str(pub_key_path), "-f", server]
    
    result = run_command(cmd, capture_output=False)
    
    if result.returncode == 0:
        console.print(f"[bold green]✓ Public key successfully copied to {server}[/bold green]")
        
        # Test the connection
        if prompt_yes_no("Test password-less SSH connection?", default=True):
            test_connection(server, private_key_path, hostname_alias, actual_hostname)
    else:
        console.print(f"[red]Failed to copy key to {server}[/red]")
        console.print("[yellow]You can manually copy the public key or try again.[/yellow]")
        if prompt_yes_no("Try manual distribution method?", default=False):
            manual_key_distribution(server, pub_key_path, private_key_path)


def manual_key_distribution(server: str, pub_key_path: Path, private_key_path: Path):
    """Manually distribute the key by appending to authorized_keys."""
    pub_key_content = pub_key_path.read_text().strip()
    
    console.print(f"\n[cyan]Manual key distribution to {server}...[/cyan]")
    console.print("[dim]This will append your public key to ~/.ssh/authorized_keys on the remote server.[/dim]")
    
    # Use ssh to append the key
    cmd = [
        "ssh", server,
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{pub_key_content}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    ]
    
    console.print("[dim]You may be prompted for the remote server password.[/dim]")
    result = run_command(cmd, capture_output=False)
    
    if result.returncode == 0:
        console.print(f"[bold green]✓ Public key successfully added to {server}[/bold green]")
        if prompt_yes_no("Test password-less SSH connection?", default=True):
            # Extract hostname from server for test_connection
            if "@" in server:
                _, test_hostname = server.split("@", 1)
            else:
                test_hostname = server
            test_connection(server, private_key_path, test_hostname, test_hostname)
    else:
        console.print(f"[red]Failed to add key to {server}[/red]")
        console.print("\n[yellow]You can manually add the key by running:[/yellow]")
        console.print(f"[dim]ssh {server} 'mkdir -p ~/.ssh && echo \"{pub_key_content}\" >> ~/.ssh/authorized_keys'[/dim]")


def test_connection(server: str, private_key_path: Path, hostname_alias: Optional[str] = None, actual_hostname: Optional[str] = None):
    """
    Test the SSH connection using the new key.
    
    Args:
        server: Server string in format user@hostname
        private_key_path: Path to the private key
        hostname_alias: Optional SSH config alias (if different from actual hostname)
        actual_hostname: Optional actual hostname/IP (if different from alias)
    """
    console.print(f"\n[cyan]Testing SSH connection to {server}...[/cyan]")
    
    # Parse server to extract user and hostname
    if "@" in server:
        user, hostname = server.split("@", 1)
    else:
        user = os.getlogin()
        hostname = server
    
    # Use provided alias/hostname if available, otherwise use parsed values
    config_alias = hostname_alias if hostname_alias else hostname
    config_hostname = actual_hostname if actual_hostname else hostname
    
    # Test with the specific key
    cmd = [
        "ssh", "-i", str(private_key_path),
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",  # Non-interactive, will fail if password needed
        server,
        "echo 'Connection successful!'"
    ]
    
    result = run_command(cmd, capture_output=True)
    
    if result.returncode == 0:
        console.print("[bold green]✓ Password-less SSH connection successful![/bold green]")
        console.print(f"[dim]You can now connect using: ssh -i {private_key_path} {server}[/dim]")
        
        # Offer to configure SSH config for easier access
        if prompt_yes_no(f"\nConfigure SSH config to allow 'ssh {config_alias}' (without user@)?", default=True):
            configure_ssh_config(config_alias, user, private_key_path, config_hostname)
    else:
        console.print("[yellow]Connection test failed. This might be normal if:[/yellow]")
        console.print("  - The server requires password authentication for the first connection")
        console.print("  - Host key verification is required")
        console.print("  - The key hasn't been properly added yet")
        console.print(f"\n[dim]Try manually: ssh -i {private_key_path} {server}[/dim]")
        
        # Still offer SSH config setup even if test failed
        if prompt_yes_no(f"\nWould you like to configure SSH config for easier access?", default=False):
            configure_ssh_config(config_alias, user, private_key_path, config_hostname)


def configure_ssh_config(hostname: str, user: str, private_key_path: Path, hostname_ip: Optional[str] = None):
    """
    Add or update SSH config entry for the host.
    
    Args:
        hostname: The Host alias to use in SSH config
        user: The username for SSH connection
        private_key_path: Path to the private key
        hostname_ip: Optional IP address or actual hostname/IP to connect to (if different from hostname alias)
    """
    ssh_config_path = Path.home() / ".ssh" / "config"
    ssh_config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    
    # Read existing config
    config_content = ""
    if ssh_config_path.exists():
        config_content = ssh_config_path.read_text()
    
    # Determine the actual HostName to use
    actual_hostname = hostname_ip if hostname_ip else hostname
    
    # Check if host entry already exists
    host_pattern = f"Host {hostname}"
    if host_pattern in config_content:
        console.print(f"[yellow]SSH config already has an entry for '{hostname}'[/yellow]")
        if prompt_yes_no("Update the existing entry?", default=True):
            # Remove old entry
            lines = config_content.splitlines()
            new_lines = []
            skip_until_next_host = False
            for line in lines:
                if line.strip().startswith("Host ") and hostname in line:
                    skip_until_next_host = True
                    continue
                if skip_until_next_host and line.strip().startswith("Host "):
                    skip_until_next_host = False
                if not skip_until_next_host:
                    new_lines.append(line)
            config_content = "\n".join(new_lines)
        else:
            return
    
    # Add new entry
    entry = f"""
Host {hostname}
    HostName {actual_hostname}
    User {user}
    IdentityFile {private_key_path}
    IdentitiesOnly yes
"""
    
    # Append to config
    if config_content and not config_content.endswith("\n"):
        config_content += "\n"
    config_content += entry
    
    # Write config
    ssh_config_path.write_text(config_content)
    ssh_config_path.chmod(0o600)  # Secure permissions
    
    console.print(f"[bold green]✓ SSH config updated![/bold green]")
    console.print(f"[dim]You can now connect using: ssh {hostname}[/dim]")
    if hostname_ip and hostname_ip != hostname:
        console.print(f"[dim]Connects to: {hostname_ip}[/dim]")
    console.print(f"[dim]Config location: {ssh_config_path}[/dim]")
    
    # Offer to clean known_hosts if IP/hostname changed
    if prompt_yes_no("Remove old entries from ~/.ssh/known_hosts for this host?", default=False):
        clean_known_hosts(hostname, actual_hostname)


def clean_known_hosts(hostname: str, actual_hostname: str):
    """Remove entries from known_hosts for the given hostname/IP."""
    known_hosts_path = Path.home() / ".ssh" / "known_hosts"
    
    if not known_hosts_path.exists():
        console.print("[dim]No known_hosts file found.[/dim]")
        return
    
    try:
        content = known_hosts_path.read_text()
        lines = content.splitlines()
        new_lines = []
        removed_count = 0
        
        for line in lines:
            # Skip comments and empty lines
            if line.strip().startswith("#") or not line.strip():
                new_lines.append(line)
                continue
            
            # Check if line contains the hostname or IP
            parts = line.split()
            if parts:
                hosts = parts[0].split(",")
                # Check if any host in this line matches our hostname/IP
                should_remove = False
                for host in hosts:
                    # Remove port if present (hostname:port)
                    host_clean = host.split(":")[0]
                    if host_clean == hostname or host_clean == actual_hostname:
                        should_remove = True
                        removed_count += 1
                        break
                
                if not should_remove:
                    new_lines.append(line)
        
        if removed_count > 0:
            known_hosts_path.write_text("\n".join(new_lines))
            console.print(f"[green]✓ Removed {removed_count} old entry/entries from known_hosts[/green]")
        else:
            console.print("[dim]No matching entries found in known_hosts[/dim]")
            
    except Exception as e:
        console.print(f"[yellow]Warning: Could not clean known_hosts: {e}[/yellow]")

if __name__ == "__main__":
    main()

