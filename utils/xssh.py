#!/usr/bin/env python3
"""
@pas-executable
Smart SSH wrapper that automatically detects if a Cloudflare Tunnel is needed.
Usage: xssh [user@]hostname [ssh_args...]
"""

import shlex
import sys
import subprocess
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    detect_cloudflared_binary,
    is_cloudflare_host,
    load_pas_config,
    save_pas_config,
)
from rich.panel import Panel

# --- Configuration ---
CONNECT_TIMEOUT = 15
SSHS_CONFIG_SERVICE = "sshs"
DEFAULT_REMOTE_SHELL = "zsh"
# SSH options that consume the next argv token (so we don't treat it as target)
SSH_OPTIONS_WITH_VALUE = frozenset(("-i", "-o", "-L", "-R", "-D", "-l", "-p", "-E", "-F", "-J", "-S", "-c", "-m", "-M", "-Q", "-w"))
# ---------------------


def _find_target_and_args(argv: list[str]) -> tuple[str | None, list[str], list[str]]:
    """
    Find the first connection target in argv (user@host or hostname) and split into
    leading_ssh_args, target, trailing_ssh_args. Returns (target, leading, trailing)
    or (None, [], []) if no target found.
    """
    if not argv:
        return None, [], []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in SSH_OPTIONS_WITH_VALUE:
            i += 2  # skip option and its value
            continue
        if tok.startswith("-"):
            i += 1
            continue
        # tok does not start with - and is not a known option value -> treat as target
        target = tok
        leading = argv[:i]
        trailing = argv[i + 1:]
        return target, leading, trailing
    return None, [], []


def _parse_local_ports_from_l_args(ssh_args: list[str]) -> list[int]:
    """Parse -L specs and return list of local port numbers. -L can be '-L spec' or next token."""
    ports = []
    i = 0
    while i < len(ssh_args):
        arg = ssh_args[i]
        if arg == "-L":
            if i + 1 < len(ssh_args):
                spec = ssh_args[i + 1]
                # [bind_address:]port:host:hostport -> local port is first or second field
                parts = spec.split(":")
                if len(parts) >= 2:
                    # port:host:hostport -> port is parts[0]
                    # bind:port:host:hostport -> port is parts[1]
                    try:
                        port = int(parts[1]) if len(parts) == 4 else int(parts[0])
                        if 1 <= port <= 65535:
                            ports.append(port)
                    except ValueError:
                        pass
                i += 2
            else:
                i += 1
        else:
            i += 1
    return ports


def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]xssh[/bold cyan] (Extended SSH) is a smart wrapper around [bold]ssh[/bold].\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Smart Detection:[/bold] Automatically checks if the host is a Cloudflare Tunnel.\n"
        "• [bold]Zero-Config:[/bold] Uses `cloudflared access ssh` ProxyCommand only when needed.\n"
        "• [bold]Remote commands:[/bold] Wraps remote commands in a login, interactive shell (default: zsh -l -i -c) so .zshrc and PATH (e.g. pas) are available.\n"
        "• [bold]Fallback:[/bold] Transparently falls back to standard `ssh` for non-tunnel hosts.\n"
        "• [bold]Transparency:[/bold] Passes all additional arguments directly to the underlying `ssh` command."
    )
    console.print(Panel(summary, title="Smart SSH Wrapper", expand=False))


def _get_remote_shell(cli_shell: str | None, set_shell: str | None) -> str:
    """Resolve remote shell: CLI override, then saved preference in ~/.pas/sshs.json, else default."""
    if set_shell:
        return set_shell
    if cli_shell:
        return cli_shell
    config = load_pas_config(SSHS_CONFIG_SERVICE)
    return config.get("shell", DEFAULT_REMOTE_SHELL)


def main():
    if len(sys.argv) < 2 or "-h" in sys.argv or "--help" in sys.argv:
        show_summary()
        console.print("\n[bold yellow]Usage:[/bold yellow] xssh [user@]hostname [ssh_args...] [--cf] [--no-cf] [--shell SHELL] [--set-shell SHELL]")
        sys.exit(0 if "-h" in sys.argv or "--help" in sys.argv else 1)

    # Target can appear anywhere in argv (e.g. xssh -N -L 8080:127.0.0.1:8080 user@host)
    target, leading, trailing = _find_target_and_args(sys.argv[1:])
    if not target:
        show_summary()
        console.print("\n[bold red]No connection target (user@host or hostname) found in arguments.[/bold red]")
        sys.exit(1)
    ssh_args = leading + trailing

    # Flag checks (xssh-only; strip from ssh_args so they are not passed to ssh)
    force_cf = "--cf" in ssh_args
    force_no_cf = "--no-cf" in ssh_args
    no_profile = "--no-profile" in ssh_args
    if force_cf:
        ssh_args.remove("--cf")
    if force_no_cf:
        ssh_args.remove("--no-cf")
    if no_profile:
        ssh_args.remove("--no-profile")

    debug_mode = "--debug" in ssh_args
    if debug_mode:
        ssh_args.remove("--debug")

    # Handle --tty / -t flag for TTY allocation
    force_tty = False
    if "--tty" in ssh_args:
        ssh_args.remove("--tty")
        force_tty = True
    if "-t" in ssh_args:
        ssh_args.remove("-t")
        force_tty = True

    cli_shell = None
    set_shell = None
    if "--shell" in ssh_args:
        i = ssh_args.index("--shell")
        ssh_args.pop(i)
        if i < len(ssh_args):
            cli_shell = ssh_args.pop(i)
    if "--set-shell" in ssh_args:
        i = ssh_args.index("--set-shell")
        ssh_args.pop(i)
        if i < len(ssh_args):
            set_shell = ssh_args.pop(i)
            config = load_pas_config(SSHS_CONFIG_SERVICE)
            config["shell"] = set_shell
            save_pas_config(SSHS_CONFIG_SERVICE, config)

    remote_shell = _get_remote_shell(cli_shell, set_shell)

    # Extract -i and key from ssh_args BEFORE wrapping, so they go to SSH not the remote command
    identity_opts = []
    if "-i" in ssh_args:
        idx = ssh_args.index("-i")
        if idx + 1 < len(ssh_args):
            identity_opts = ["-i", ssh_args[idx + 1]]
            ssh_args = [a for i, a in enumerate(ssh_args) if i != idx and i != idx + 1]

    # Only wrap in shell -l -i -c when there is a remote command. Do NOT wrap for port-forwarding only (-N -L etc.).
    if ssh_args and "-N" not in ssh_args:
        cmd_str = " ".join(ssh_args)
        remote_cmd = f"{remote_shell} -l -i -c {shlex.quote(cmd_str)}"
        ssh_args = [remote_cmd]
        if debug_mode:
            console.print(f"[bold blue]DEBUG:[/bold blue] Wrapped remote command with: {remote_shell} -l -i -c")

    # Smart detection with overrides
    if force_cf:
        use_cf = True
    elif force_no_cf:
        use_cf = False
    else:
        use_cf = is_cloudflare_host(target)
    
    if debug_mode:
        console.print(f"[bold blue]DEBUG:[/bold blue] Target: {target}")
        console.print(f"[bold blue]DEBUG:[/bold blue] Cloudflare detected: {use_cf}")

    # --- Profile Management ---
    config = load_pas_config(SSHS_CONFIG_SERVICE)
    profiles = config.get("profiles", {})

    # Auto-load saved key if not provided on CLI and not --no-profile
    if not no_profile and not identity_opts:
        # Check for profile matching the target
        if target in profiles:
            saved_key = profiles[target].get("identity_file")
            if saved_key and Path(saved_key).exists():
                identity_opts = ["-i", saved_key]
                if debug_mode:
                    console.print(f"[bold blue]DEBUG:[/bold blue] Auto-loaded saved key for {target}: {saved_key}")
        else:
            # Fallback: check if target is just a hostname and we have a user@host profile
            # or vice versa. xssh often gets called with just hostname if config exists.
            for profile_name, data in profiles.items():
                if "@" in profile_name:
                    p_user, p_host = profile_name.split("@", 1)
                    if p_host == target:
                        saved_key = data.get("identity_file")
                        # Update target to include the user
                        target = profile_name
                        if saved_key and Path(saved_key).exists():
                            identity_opts = ["-i", saved_key]
                            if debug_mode:
                                console.print(f"[bold blue]DEBUG:[/bold blue] Auto-loaded user profile {target}: {saved_key}")
                        else:
                            if debug_mode:
                                console.print(f"[bold blue]DEBUG:[/bold blue] Found user profile {target}, switching target.")
                        break

    # If a user@host was provided on CLI, ensure it's saved as a profile even without -i
    # but only if not --no-profile
    if not no_profile and "@" in target and target not in profiles and identity_opts:
        profiles[target] = profiles.get(target, {})
        idx = identity_opts.index("-i") if "-i" in identity_opts else -1
        if idx >= 0 and idx + 1 < len(identity_opts):
            profiles[target]["identity_file"] = str(Path(identity_opts[idx + 1]).expanduser().resolve())
        config["profiles"] = profiles
        save_pas_config(SSHS_CONFIG_SERVICE, config)
        if debug_mode:
            console.print(f"[bold blue]DEBUG:[/bold blue] Registered new profile for {target}")

    # Helper to run SSH and check for success
    # Identity options (-i key) MUST come before target; remote command comes after.
    def run_ssh(target_host, args_list, identity_opts=None, disable_password=False):
        if use_cf:
            cloudflared_path = detect_cloudflared_binary()
            if cloudflared_path:
                proxy_cmd = f"{cloudflared_path} access ssh --hostname %h"
                cmd = ["ssh", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}", "-o", f"ProxyCommand={proxy_cmd}"]
            else:
                cmd = ["ssh"]
        else:
            cmd = ["ssh"]

        if disable_password:
            # Disable password/interactive auth to force failure if keys don't work
            # This allows our recovery menu to trigger instead of hanging at a password prompt.
            cmd.extend(["-o", "KbdInteractiveAuthentication=no", "-o", "PasswordAuthentication=no"])

        if force_tty:
            cmd.append("-t")

        # Identity options MUST come before target for SSH to use them
        if identity_opts:
            cmd.extend(identity_opts)

        cmd.append(target_host)
        cmd.extend(args_list)

        if debug_mode:
            console.print(f"[bold blue]DEBUG:[/bold blue] Running: {' '.join(cmd)}")

        return subprocess.run(cmd)

    # When -L is used, print link before connecting so user can open after tunnel is up
    local_ports = _parse_local_ports_from_l_args(ssh_args)
    if local_ports:
        for port in local_ports[:3]:  # show up to first 3
            console.print(f"When connected, open: [bold]http://127.0.0.1:{port}[/bold]")

    # Try initial connection
    # If --no-profile is passed, we allow password auth immediately to behave like standard SSH.
    # Otherwise, we disable it to trigger our "Pick a Key" recovery menu if keys fail.
    res = run_ssh(target, ssh_args, identity_opts=identity_opts, disable_password=not no_profile)
    
    # If connection failed (likely auth error 255) and we hadn't already allowed passwords
    if res.returncode == 255 and not no_profile:
        console.print("\n[yellow]Key authentication failed.[/yellow]")
        from helpers.core import get_ssh_keys, format_menu_choices, prompt_toolkit_menu
        
        keys = get_ssh_keys()
        key_choices = []
        if keys:
            key_choices = [{"title": f"Use key: {k.name}", "value": str(k)} for k in keys]
        
        key_choices.append({"title": "Fall back to standard SSH (allows password)", "value": "fallback"})
        key_choices.append({"title": "[Quit]", "value": "quit"})
        
        formatted = format_menu_choices(key_choices)
        console.print("[bold]Select an authentication method:[/bold]")
        selected_key = prompt_toolkit_menu(formatted)
        
        if selected_key == "quit":
            sys.exit(res.returncode)
        elif selected_key == "fallback" or not selected_key:
            # Retry with password auth enabled, no identity (let SSH use defaults)
            console.print("Retrying with standard SSH...")
            res = run_ssh(target, ssh_args, identity_opts=[], disable_password=False)
        else:
            console.print(f"Retrying with key: {selected_key}")
            # Use --tty for the retry to allow password fallbacks if the key still fails
            force_tty = True
            res = run_ssh(target, ssh_args, identity_opts=["-i", selected_key], disable_password=False)
            
            # If successful, save the key
            if res.returncode == 0:
                profiles[target] = profiles.get(target, {})
                profiles[target]["identity_file"] = str(Path(selected_key).expanduser().resolve())
                config["profiles"] = profiles
                save_pas_config(SSHS_CONFIG_SERVICE, config)
                console.print(f"[green][✓] Saved working SSH key profile for {target}[/green]")

    # If we had a key in identity_opts and it worked, ensure it's saved
    if res.returncode == 0 and identity_opts and "-i" in identity_opts:
        idx = identity_opts.index("-i")
        if idx + 1 < len(identity_opts):
            working_key = identity_opts[idx + 1]
            resolved_key = str(Path(working_key).expanduser().resolve())
            if target not in profiles or profiles[target].get("identity_file") != resolved_key:
                profiles[target] = profiles.get(target, {})
                profiles[target]["identity_file"] = resolved_key
                config["profiles"] = profiles
                save_pas_config(SSHS_CONFIG_SERVICE, config)
                console.print(f"[green][✓] Profile updated with working key for {target}[/green]")

    sys.exit(res.returncode)
    # --------------------------

if __name__ == "__main__":
    main()
