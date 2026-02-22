#!/usr/bin/env python3
"""
@pas-executable
Manage headless accounts (create, delete, list, hide/unhide) on macOS.
Supports local and remote management via xssh.

LESSONS LEARNED (Modern macOS Sequoia/Sonoma):
1. HEADLESS STRATEGY: 
   - DO NOT use 'pwpolicy -disableuser'. It locks the account at the kernel level and breaks SSH.
   - USE 'dscl IsHidden 1' instead. It hides the user from the GUI but keeps SSH alive.
   - ALWAYS ensure the user is in the 'com.apple.access_ssh' group via dseditgroup.

2. ACCOUNT RECOVERY:
   - If an account is "hard-locked", use 'dscl Password "*"' and delete 'AuthenticationAuthority' 
     to "wake up" the record before applying pwpolicy or SSH keys.

3. REMOTE EXECUTION:
   - Chain commands with '&&' and wrap in 'sh -c' to ensure sudo applies to the entire chain.
   - This achieves a "Single Password Prompt" workflow for complex remote setups.
   - Use '--tty' for interactive commands (sudo prompts) and clean output (no TTY) for parsing.

4. KEY MANAGEMENT:
   - Use 'id_ed25519_<user>_at_<host>' to prevent collisions when the same service account 
     exists across multiple remote servers.
   - SSH is strict: Home and .ssh must be 700, authorized_keys must be 600.
"""

import os
import sys
import argparse
import subprocess
import tempfile
import socket
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    prompt_yes_no,
    format_menu_choices,
    prompt_toolkit_menu,
    run_command,
    load_pas_config,
    save_pas_config
)
from rich.panel import Panel
from rich.table import Table

# --- Configuration ---
DEFAULT_SHELL = "/bin/zsh"
STAFF_GROUP_ID = 20
ADMIN_GROUP_ID = 80
MIN_UID = 501
SSHS_CONFIG_SERVICE = "sshs"
# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "user-ops"
TOOL_TITLE = "User Management Operations"
TOOL_SHORT_DESC = "Manage headless accounts (create, delete, list, hide/unhide) on macOS. Local and remote via xssh."
TOOL_DESCRIPTION = "Manage headless accounts (create, delete, list, hide/unhide) on macOS. Supports local and remote via xssh."
# ---------------------

class CommandRunner:
    """Base class for executing system commands."""
    def run(self, cmd: List[str], capture_output: bool = True, use_sudo: bool = True) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def is_local(self) -> bool:
        raise NotImplementedError

class LocalRunner(CommandRunner):
    """Executes commands on the local machine."""
    def run(self, cmd: List[str], capture_output: bool = True, use_sudo: bool = True) -> subprocess.CompletedProcess:
        if use_sudo and os.getuid() != 0:
            cmd = ["sudo"] + cmd
        return run_command(cmd, capture_output=capture_output)
    
    def is_local(self) -> bool:
        return True

class RemoteRunner(CommandRunner):
    """Executes commands on a remote machine via xssh."""
    def __init__(self, host: str):
        self.host = host
        self._remote_user = None
        self._sudo_session_active = False
        # Handle user@host format
        if "@" in host:
            self._remote_user, self.actual_host = host.split("@", 1)
        else:
            self.actual_host = host

    def run(self, cmd: List[str], capture_output: bool = True, use_sudo: bool = True) -> subprocess.CompletedProcess:
        # Wrap in xssh and optionally sudo
        import shlex
        cmd_str = " ".join(cmd)
        
        prefix = "sudo " if use_sudo else ""
        
        # If we think sudo might need a password, we can try to validate the session once
        if use_sudo and not self._sudo_session_active:
            console.print(f"\n[bold yellow]🔒 PRIVILEGE ELEVATION REQUIRED[/bold yellow]")
            console.print(f"[dim]The following operation requires sudo on {self.host}:[/dim]")
            console.print(f"  [cyan]{' '.join(cmd)}[/cyan]")
            console.print(f"[dim]Please enter the password for the remote account.[/dim]\n")
            
            # Run a simple sudo command once to trigger the password prompt
            # We use -v (validate) which updates the sudo timestamp
            validate_cmd = ["xssh", self.host, "--tty", "sudo -v"]
            run_command(validate_cmd, capture_output=False)
            self._sudo_session_active = True

        # Build the xssh command
        # xssh [user@]hostname [ssh_args...]
        full_cmd = ["xssh", self.host]
        
        # If we need a TTY, we pass --tty as an argument to xssh
        if not capture_output:
            full_cmd.append("--tty") # Use a clearer flag name for xssh
            
        full_cmd.append(f"{prefix}{cmd_str}")
        
        res = run_command(full_cmd, capture_output=capture_output)
        
        # If we captured output, clean up any \r characters just in case
        if capture_output and res.stdout:
            cleaned_stdout = res.stdout.replace('\r\n', '\n').replace('\r', '\n').strip()
            res = subprocess.CompletedProcess(
                args=res.args,
                returncode=res.returncode,
                stdout=cleaned_stdout,
                stderr=res.stderr
            )
            
        return res

    def is_local(self) -> bool:
        return False

    def get_remote_user(self) -> str:
        """Get the username of the account we are logged into on the remote Mac."""
        if self._remote_user is None:
            # Try 'id -un' as it's more standard than 'whoami'
            # We try WITHOUT sudo first to get the actual logged-in user
            res = run_command(["xssh", self.host, "id -un"], capture_output=True)
            if res.returncode == 0 and res.stdout.strip():
                self._remote_user = res.stdout.strip()
            else:
                # Try whoami as fallback
                res = run_command(["xssh", self.host, "whoami"], capture_output=True)
                if res.returncode == 0 and res.stdout.strip():
                    self._remote_user = res.stdout.strip()
                else:
                    console.print(f"[yellow]Warning: Could not automatically detect remote master account on {self.host}.[/yellow]")
                    self._remote_user = input("Please enter the remote master username: ").strip()
                    if not self._remote_user:
                        self._remote_user = "remote_master"
        return self._remote_user

def show_summary(runner: CommandRunner):
    """Display a brief summary of the tool's capabilities."""
    target_desc = "local machine" if runner.is_local() else f"remote host [bold]{runner.host}[/bold]"
    summary = (
        f"[bold cyan]user-ops[/bold cyan]: {TOOL_DESCRIPTION} On {target_desc}.\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Headless Creation:[/bold] Creates accounts optimized for SSH/background services.\n"
        "• [bold]GUI Invisibility:[/bold] Hides accounts from the login screen (macOS).\n"
        "• [bold]Security:[/bold] Disables console login while maintaining SSH access.\n"
        "• [bold]Remote Management:[/bold] Manage accounts on remote Macs via [bold]--remote[/bold].\n"
        "• [bold]Auto-SSH:[/bold] Generates and links SSH keys for instant access."
    )
    console.print(Panel(summary, title=TOOL_TITLE, expand=False))

def check_root(runner: CommandRunner):
    """Ensure we have root privileges. If local and not root, re-run with sudo."""
    if runner.is_local():
        if os.getuid() != 0:
            console.print("[yellow]This script requires root privileges. Re-running with sudo...[/yellow]")
            try:
                subprocess.run(["sudo", sys.executable] + sys.argv, check=True)
                sys.exit(0)
            except (subprocess.CalledProcessError, KeyboardInterrupt):
                sys.exit(1)
    # For RemoteRunner, sudo is handled inside the run() method

def list_users(runner: CommandRunner) -> List[Dict[str, Any]]:
    """List non-system users (UID >= 500)."""
    # Try listing users WITHOUT sudo first, as it's cleaner and usually allowed for -list
    res = runner.run(["dscl", ".", "-list", "/Users", "UniqueID"], capture_output=True, use_sudo=False)
    
    if res.returncode != 0 or not res.stdout.strip():
        # If that fails, try WITH sudo
        res = runner.run(["dscl", ".", "-list", "/Users", "UniqueID"], capture_output=True, use_sudo=True)
        
    if res.returncode != 0 or not res.stdout.strip():
        # Fallback to readall
        res = runner.run(["dscl", ".", "-readall", "/Users", "UniqueID"], capture_output=True, use_sudo=False)
        if res.returncode != 0 or not res.stdout.strip():
            res = runner.run(["dscl", ".", "-readall", "/Users", "UniqueID"], capture_output=True, use_sudo=True)
        
    if res.returncode != 0:
        return []
        
    if res.returncode != 0:
        return []
    
    users = []
    # Parse the output. dscl -list returns "user uid" per line.
    # dscl -readall returns "RecordName: user\nUniqueID: uid" blocks.
    
    raw_output = res.stdout.strip()
    if not raw_output:
        return []

    if "RecordName:" in raw_output:
        # Parsing readall output
        current_user = {}
        for line in raw_output.splitlines():
            if line.startswith("RecordName:"):
                current_user["username"] = line.split(":", 1)[1].strip()
            elif line.startswith("UniqueID:"):
                try:
                    current_user["uid"] = int(line.split(":", 1)[1].strip())
                    if current_user.get("username"):
                        users.append(current_user.copy())
                    current_user = {}
                except ValueError:
                    continue
    else:
        # Parsing list output
        for line in raw_output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                username = parts[0]
                try:
                    uid = int(parts[-1]) # UID is usually the last part
                    users.append({"username": username, "uid": uid})
                except ValueError:
                    continue
    
    final_users = []
    for u in users:
        username = u["username"]
        uid = u["uid"]
        
        # macOS standard users are >= 500. 
        # We also want to ensure we show the remote master account even if it's special.
        # But we filter out system accounts starting with underscore.
        if (uid >= 500 or username == runner.get_remote_user() if not runner.is_local() else False) and not username.startswith("_"):
            # Get more info
            hidden_res = runner.run(["dscl", ".", "-read", f"/Users/{username}", "IsHidden"], capture_output=True)
            is_hidden = "IsHidden: 1" in hidden_res.stdout
            
            realname_res = runner.run(["dscl", ".", "-read", f"/Users/{username}", "RealName"], capture_output=True)
            realname = ""
            if "RealName:" in realname_res.stdout:
                # RealName can be multi-line or have a prefix
                parts = realname_res.stdout.split("RealName:", 1)
                if len(parts) > 1:
                    realname = parts[1].strip().splitlines()[0] # Take first line
            
            final_users.append({
                "username": username,
                "uid": uid,
                "hidden": is_hidden,
                "realname": realname
            })
            
    return sorted(final_users, key=lambda x: x["uid"])

def create_user(runner: CommandRunner, username: str, password: str, fullname: Optional[str] = None, admin: bool = False, hidden: bool = True, auto_ssh: bool = False):
    """Create a headless user."""
    console.print(f"Creating user [bold]{username}[/bold]...")
    
    # We use a chained command for creation as well to ensure all definitive fixes are applied from the start
    # We include PasswordPolicyOptions to ensure linger and password modification are enabled.
    create_cmds = [
        f"sysadminctl -addUser {username} -password {password}",
        f"dscl . -create /Users/{username} UserShell {DEFAULT_SHELL}",
        f"dscl . -delete /Users/{username} AuthenticationAuthority 2>/dev/null || true",
        f"pwpolicy -u {username} -setpolicy is-disabled=0",
        f"pwpolicy -u {username} -enableuser",
        f"dscl . -create /Users/{username} IsHidden 1",
        f"dscl . -create /Users/{username} PasswordPolicyOptions '{{ \"canModifyPasswordforSelf\" = 1; \"isLoginDisabled\" = 0; \"isLingerEnabled\" = 1; }}'",
        f"dseditgroup -o edit -a {username} -t user com.apple.access_ssh 2>/dev/null || true",
        f"mkdir -p /Users/{username}",
        f"chmod 700 /Users/{username}",
        f"chown -R {username}:staff /Users/{username}"
    ]
    
    if fullname:
        # Update full name separately as sysadminctl might have already set it
        create_cmds.insert(1, f"dscl . -create /Users/{username} RealName '{fullname}'")
    if admin:
        create_cmds.append(f"dseditgroup -o edit -a {username} -t user admin")

    chained_create = " && ".join(create_cmds)
    res = runner.run(["sh", "-c", chained_create], capture_output=False)
    if res.returncode != 0:
        console.print(f"[bold red]Error creating user.[/bold red]")
        return False
    
    console.print(f"[bold green]Success![/bold green] User {username} created and optimized for headless SSH.")
    console.print(f"• Login Shell: {DEFAULT_SHELL}")
    console.print(f"• SSH Access: [bold green]Enabled & Verified[/bold green]")
    console.print(f"• GUI Login: [bold red]Disabled (Hidden)[/bold red]")
    
    # Check if system-wide Remote Login is enabled
    rl_res = runner.run(["systemsetup", "-getremotelogin"])
    if "Remote Login: On" not in rl_res.stdout:
        should_enable = auto_ssh
        if not auto_ssh and sys.stdin.isatty():
            should_enable = prompt_yes_no("Remote Login (SSH) is currently OFF. Enable it now?", default=True)
            
        if should_enable:
            console.print("Enabling Remote Login...")
            runner.run(["systemsetup", "-setremotelogin", "on"])
            console.print("[green][✓] Remote Login enabled.[/green]")
        else:
            console.print("[yellow]Warning: Remote Login (SSH) remains OFF.[/yellow]")
            console.print("To enable it manually, run: [bold]sudo systemsetup -setremotelogin on[/bold]")
    
    return True

def delete_user(runner: CommandRunner, username: str):
    """Delete a user."""
    console.print(f"\n[bold red]⚠️  WARNING: You are about to delete the user account: {username}[/bold red]")
    console.print(f"[red]This action is permanent and will delete the home directory.[/red]\n")
    
    confirm = input(f"To confirm, please type the username [bold]{username}[/bold]: ").strip()
    
    if confirm != username:
        console.print("[yellow]Deletion cancelled: Username mismatch.[/yellow]")
        return False
    
    res = runner.run(["sysadminctl", "-deleteUser", username], capture_output=False)
    if res.returncode == 0:
        console.print(f"[bold green]Deleted user {username}.[/bold green]")
        return True
    else:
        console.print(f"[bold red]Error deleting user.[/bold red]")
        return False

def set_hidden(runner: CommandRunner, username: str, hidden: bool):
    """Toggle IsHidden attribute."""
    val = "1" if hidden else "0"
    res = runner.run(["dscl", ".", "-create", f"/Users/{username}", "IsHidden", val], capture_output=False)
    if res.returncode == 0:
        status = "hidden" if hidden else "visible"
        console.print(f"[bold green]User {username} is now {status}.[/bold green]")
        return True
    else:
        console.print(f"[bold red]Error updating user.[/bold red]")
        return False

def setup_ssh_keys(runner: CommandRunner, username: str):
    """Setup SSH keys for passwordless login."""
    local_user = os.getlogin()
    
    # 1. Determine the remote master account name if in remote mode
    remote_master = None
    if not runner.is_local():
        remote_master = runner.get_remote_user()
        console.print(f"Detected remote master account: [bold]{remote_master}[/bold]")

    # 2. Generate key pair on LOCAL Mac
    master_ssh_dir = Path.home() / ".ssh"
    master_ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    
    if runner.is_local():
        key_name = f"id_ed25519_{username}_local"
    else:
        # Use hostname to keep keys for the same username on different servers distinct
        host_clean = runner.host.split("@")[-1].split(".")[0] # Get short hostname
        key_name = f"id_ed25519_{username}_at_{host_clean}"
        
    key_path = master_ssh_dir / key_name
    pub_key_path = key_path.with_suffix(".pub")
    
    if not key_path.exists():
        console.print(f"Generating SSH key pair for {username}...")
        comment = f"{local_user} to {username} ({key_name})"
        if remote_master:
            comment = f"{local_user} via {remote_master} to {username} on {runner.host}"
            
        run_command([
            "ssh-keygen", "-t", "ed25519",
            "-f", str(key_path),
            "-N", "",
            "-C", comment
        ])
    
    pub_key = pub_key_path.read_text().strip()
    
    # 3. Setup .ssh on TARGET Mac (Local or Remote)
    ssh_dir = f"/Users/{username}/.ssh"
    auth_keys = f"{ssh_dir}/authorized_keys"
    
    console.print(f"Configuring .ssh for {username} on target...")
    
    # Prepare the content for authorized_keys
    authorized_keys_content = pub_key + "\n"
    
    # If in remote mode, also add the remote master's public key
    if remote_master:
        console.print(f"Linking remote master account [bold]{remote_master}[/bold] to {username}...")
        res = runner.run(["cat", f"~{remote_master}/.ssh/id_ed25519.pub"], capture_output=True, use_sudo=False)
        if res.returncode != 0:
            res = runner.run(["cat", f"~{remote_master}/.ssh/id_rsa.pub"], capture_output=True, use_sudo=False)
            
        if res.returncode == 0:
            master_pub_key = res.stdout.strip()
            authorized_keys_content += master_pub_key + "\n"
            console.print(f"[green][✓] Included remote master's key.[/green]")

    # To avoid multiple password prompts, we chain all setup commands into a single remote call
    # We use a temporary file on the remote to avoid complex escaping with heredocs
    if runner.is_local():
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        with open(auth_keys, "w") as f:
            f.write(authorized_keys_content)
        os.chmod(auth_keys, 0o600)
    else:
        # 1. Write content to a local temp file
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
            tf.write(authorized_keys_content)
            local_temp = tf.name
            
        # 2. xscp it to a public temp location on remote
        remote_temp = f"/tmp/ssh_setup_{username}"
        console.print(f"Uploading SSH configuration...")
        run_command(["xscp", local_temp, f"{runner.host}:{remote_temp}"])
        os.unlink(local_temp)
        
        # 3. Run a single chained sudo command to move and set permissions
        setup_cmds = (
            f"mkdir -p {ssh_dir} && "
            f"chmod 700 {ssh_dir} && "
            f"mv {remote_temp} {auth_keys} && "
            f"chmod 600 {auth_keys} && "
            f"chown -R {username}:staff /Users/{username}"
        )
        
        console.print(f"Applying permissions on remote...")
        runner.run(["sh", "-c", f"\"{setup_cmds}\""], capture_output=False)
    
    # 4. Register xssh profile locally
    config = load_pas_config(SSHS_CONFIG_SERVICE)
    profiles = config.get("profiles", {})
    
    if runner.is_local():
        target_alias = f"{username}-local"
        profiles[target_alias] = {"identity_file": str(key_path.resolve()), "remote_user": username, "host": "localhost"}
        console.print(f"[green][✓] Registered xssh profile: {target_alias}[/green]")
    else:
        host_only = runner.host
        if "@" in host_only:
            host_only = host_only.split("@", 1)[1]
            
        target_alias = f"{username}@{host_only}"
        profiles[target_alias] = {
            "identity_file": str(key_path.resolve()),
            "remote_user": username,
            "tunnel_host": host_only,
            "source_host": runner.host
        }
        console.print(f"\n[bold green]Remote Link Complete![/bold green]")
        console.print(f"You can now login with: [bold]xssh {target_alias}[/bold]")
        
        if remote_master:
            console.print(f"Remote master [bold]{remote_master}[/bold] can login via: [bold]ssh {username}@localhost[/bold]")

    config["profiles"] = profiles
    save_pas_config(SSHS_CONFIG_SERVICE, config)

def fix_user(runner: CommandRunner, username: str):
    """Fix common issues with a headless account (shell, policy, home dir)."""
    console.print(f"Fixing user account [bold]{username}[/bold]...")
    
    # To avoid multiple password prompts, we chain all fix commands into a single remote call
    # We wrap the entire chain in 'sh -c' to ensure sudo applies to all commands correctly
    # Note: We use 'dscl' to reset the password if the account is disabled, 
    # as 'pwpolicy' can fail if the account is already in a locked state.
    # We use dscl IsHidden 1 instead of pwpolicy -disableuser to avoid SSH lockouts.
    # We also ensure the user is in the com.apple.access_ssh group and has linger enabled.
    fix_cmd = (
        f"mkdir -p /Users/{username} && "
        f"chmod 700 /Users/{username} && "
        f"dscl . -create /Users/{username} UserShell {DEFAULT_SHELL} && "
        f"dscl . -delete /Users/{username} AuthenticationHint 2>/dev/null || true && "
        f"dscl . -create /Users/{username} Password '*' && "
        f"dscl . -delete /Users/{username} AuthenticationAuthority 2>/dev/null || true && "
        f"pwpolicy -u {username} -setpolicy is-disabled=0 && "
        f"pwpolicy -u {username} -enableuser && "
        f"dscl . -create /Users/{username} IsHidden 1 && "
        f"dscl . -create /Users/{username} PasswordPolicyOptions '{{ \"canModifyPasswordforSelf\" = 1; \"isLoginDisabled\" = 0; \"isLingerEnabled\" = 1; }}' && "
        f"dseditgroup -o edit -a {username} -t user com.apple.access_ssh 2>/dev/null || true && "
        f"mkdir -p /Users/{username}/.ssh && "
        f"chmod 700 /Users/{username}/.ssh && "
        f"touch /Users/{username}/.ssh/authorized_keys && "
        f"chmod 600 /Users/{username}/.ssh/authorized_keys && "
        f"chown -R {username}:staff /Users/{username}"
    )
    
    if runner.is_local():
        # For local, we can run the commands directly or via sh -c
        # Since we are already root (or re-run with sudo), we just execute
        subprocess.run(["sh", "-c", fix_cmd], check=False)
        res = subprocess.CompletedProcess(args=[], returncode=0) # Mock success
    else:
        # Run the chained command via sh -c so sudo covers the whole subshell
        res = runner.run(["sh", "-c", f"\"{fix_cmd}\""], capture_output=False)
    
    if res.returncode == 0:
        console.print(f"[bold green]Success![/bold green] User {username} has been fixed.")
        console.print(f"• Login Shell: {DEFAULT_SHELL}")
        console.print(f"• Account Policy: [bold green]Active (SSH allowed)[/bold green]")
        console.print(f"• GUI Login: [bold red]Disabled[/bold red]")
    else:
        console.print(f"[bold red]Error fixing user.[/bold red]")

def interactive_menu(runner: CommandRunner):
    """Main interactive TUI."""
    show_summary(runner)
    
    while True:
        users = list_users(runner)
        
        target_name = "Local" if runner.is_local() else runner.host
        table = Table(title=f"Users on {target_name} (UID >= 500)")
        table.add_column("Username", style="cyan")
        table.add_column("UID", style="magenta")
        table.add_column("Real Name", style="green")
        table.add_column("Hidden", style="yellow")
        
        for u in users:
            table.add_row(u["username"], str(u["uid"]), u["realname"], "Yes" if u["hidden"] else "No")
        
        console.print(table)
        
        menu_items = [
            {"title": "Create New Headless User", "value": "create"},
            {"title": "Fix Existing User (Shell/Policy/Home)", "value": "fix_user"},
            {"title": "Delete User", "value": "delete"},
            {"title": "Toggle User Visibility (Hide/Unhide)", "value": "toggle"},
            {"title": "Setup SSH Keys for User", "value": "ssh_setup"},
        ]

        if runner.is_local():
            menu_items.append({"title": "Switch to Remote Host", "value": "switch_remote"})
        else:
            menu_items.append({"title": "Switch to Local Machine", "value": "switch_local"})

        menu_items.append({"title": "[Quit]", "value": "quit"})
        
        choice = prompt_toolkit_menu(format_menu_choices(menu_items))
        
        if not choice or choice == "quit":
            break
        
        if choice == "switch_remote":
            host = input("Enter remote host (hostname or SSH alias): ").strip()
            if host:
                runner = RemoteRunner(host)
                console.print(f"Switched to remote host: [bold]{host}[/bold]")
                remote_master = runner.get_remote_user()
                console.print(f"Detected remote master account: [bold]{remote_master}[/bold]")
                show_summary(runner)
            continue

        if choice == "switch_local":
            runner = LocalRunner()
            console.print("Switched to [bold]Local Machine[/bold]")
            show_summary(runner)
            continue

        if choice == "create":
            username = input("Enter username: ").strip()
            if not username: continue
            password = input("Enter password: ").strip()
            if not password: continue
            fullname = input("Enter full name (optional): ").strip() or None
            admin = prompt_yes_no("Grant admin privileges?", default=False)
            # Headless accounts are always hidden by default in user-ops
            if create_user(runner, username, password, fullname, admin, hidden=True):
                if prompt_yes_no(f"Setup SSH keys for passwordless login as {username}?", default=True):
                    setup_ssh_keys(runner, username)
            
        elif choice == "fix_user":
            if not users: continue
            user_choices = [{"title": u["username"], "value": u["username"]} for u in users]
            user_choices.append({"title": "[Back]", "value": "back"})
            sel_user = prompt_toolkit_menu(format_menu_choices(user_choices))
            if sel_user and sel_user != "back":
                fix_user(runner, sel_user)

        elif choice == "delete":
            if not users: continue
            user_choices = [{"title": u["username"], "value": u["username"]} for u in users]
            user_choices.append({"title": "[Back]", "value": "back"})
            sel_user = prompt_toolkit_menu(format_menu_choices(user_choices))
            if sel_user and sel_user != "back":
                delete_user(runner, sel_user)
                
        elif choice == "toggle":
            if not users: continue
            user_choices = [{"title": f"{u['username']} (Currently {'Hidden' if u['hidden'] else 'Visible'})", "value": u} for u in users]
            user_choices.append({"title": "[Back]", "value": "back"})
            sel_user_obj = prompt_toolkit_menu(format_menu_choices(user_choices))
            if sel_user_obj and sel_user_obj != "back":
                set_hidden(runner, sel_user_obj["username"], not sel_user_obj["hidden"])
        
        elif choice == "ssh_setup":
            if not users: continue
            user_choices = [{"title": u["username"], "value": u["username"]} for u in users]
            user_choices.append({"title": "[Back]", "value": "back"})
            sel_user = prompt_toolkit_menu(format_menu_choices(user_choices))
            if sel_user and sel_user != "back":
                setup_ssh_keys(runner, sel_user)

def main():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("target_host", nargs="?", help="Manage accounts on a remote host (e.g. user@host) or local if omitted")
    parser.add_argument("--remote", metavar="HOST", help="Manage accounts on a remote host via xssh (legacy, use positional arg instead)")
    parser.add_argument("--create", metavar="USERNAME", help="Create a new account")
    parser.add_argument("--password", help="Password for the new account")
    parser.add_argument("--fullname", help="Full name for the new account")
    parser.add_argument("--admin", action="store_true", help="Grant admin privileges")
    parser.add_argument("--no-hide", action="store_true", help="Do not hide the account from login screen")
    parser.add_argument("--delete", metavar="USERNAME", help="Delete an account")
    parser.add_argument("--list", action="store_true", help="List all accounts")
    parser.add_argument("--hide", metavar="USERNAME", help="Hide an account")
    parser.add_argument("--unhide", metavar="USERNAME", help="Unhide an account")
    parser.add_argument("--enable-ssh", action="store_true", help="Enable Remote Login (SSH) if it is off")
    parser.add_argument("--setup-keys", action="store_true", help="Generate and setup SSH keys")
    
    args = parser.parse_args()
    
    # Determine the host: positional arg takes priority, then --remote flag
    host = args.target_host or args.remote
    runner = RemoteRunner(host) if host else LocalRunner()
    
    if not runner.is_local():
        remote_master = runner.get_remote_user()
        console.print(f"Detected remote master account: [bold]{remote_master}[/bold]")

    # Check if any operation flags were provided
    ops_flags = ["create", "delete", "list", "hide", "unhide", "enable_ssh", "setup_keys"]
    has_ops = any(getattr(args, flag) for flag in ops_flags)

    if has_ops:
        check_root(runner)
        if args.list:
            for u in list_users(runner):
                print(f"{u['username']} (UID: {u['uid']}, Hidden: {u['hidden']})")
        elif args.create:
            password = args.password
            if not password:
                if sys.stdin.isatty():
                    import getpass
                    password = getpass.getpass("Password for new user %s: " % args.create)
                else:
                    password = sys.stdin.read().strip()
                if not password:
                    console.print("[bold red]Error:[/bold red] Password required (use --password or stdin).")
                    sys.exit(1)
            if create_user(runner, args.create, password, args.fullname, args.admin, not args.no_hide, args.enable_ssh):
                if args.setup_keys: setup_ssh_keys(runner, args.create)
        elif args.delete: delete_user(runner, args.delete)
        elif args.hide: set_hidden(runner, args.hide, True)
        elif args.unhide: set_hidden(runner, args.unhide, False)
    else:
        check_root(runner)
        interactive_menu(runner)

if __name__ == "__main__":
    main()
