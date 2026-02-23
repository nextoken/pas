#!/usr/bin/env python3
"""
@pas-executable
SSH local port-forward wrapper: manage tunnel profiles and connect with one command.
Usage: xtunnel [list] | xtunnel -p <profile_name_or_id>
"""

import argparse
import os
import signal
import sys
import subprocess
import threading
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    choice,
    console,
    format_menu_choices,
    load_pas_config,
    prompt_toolkit_menu,
    save_pas_config,
)
from rich.panel import Panel

# --- Configuration ---
XTUNNEL_CONFIG_SERVICE = "xtunnel"
DEFAULT_REMOTE_HOST = "127.0.0.1"
DEFAULT_PROTOCOL = "http"
PROTOCOL_CHOICES = ("http", "https")
# ---------------------


def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]xtunnel[/bold cyan] wraps SSH local port forwarding with saved profiles.\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Setup profiles:[/bold] user@host, remote port, local port (default same), protocol (default http), optional name.\n"
        "• [bold]Connect:[/bold] Pick a profile and run [bold]xssh -N -L ...[/bold]; link is shown and optionally opened in browser (http/https only).\n"
        "• [bold]Transparency:[/bold] Uses [bold]xssh[/bold] under the hood so Cloudflare Tunnel detection and key profiles still apply."
    )
    console.print(Panel(summary, title="SSH Tunnel Profiles", expand=False))


def _default_profile_name(target: str, local_port: int) -> str:
    return f"127.0.0.1:{local_port} on {target}"


def _load_profiles() -> dict:
    config = load_pas_config(XTUNNEL_CONFIG_SERVICE)
    return config.get("profiles", {})


def _save_profiles(profiles: dict) -> None:
    config = load_pas_config(XTUNNEL_CONFIG_SERVICE)
    config["profiles"] = profiles
    save_pas_config(XTUNNEL_CONFIG_SERVICE, config)


def _next_profile_id(profiles: dict) -> int:
    """Return next available numeric id for a new profile."""
    if not profiles:
        return 1
    return 1 + max(int(p.get("id", 0)) for p in profiles.values())


def _ensure_profile_ids(profiles: dict) -> None:
    """Assign id to any profile missing one (migration)."""
    existing_ids = {int(p.get("id", 0)) for p in profiles.values() if p.get("id") is not None}
    next_id = 1
    changed = False
    for k, p in profiles.items():
        if p.get("id") is None:
            while next_id in existing_ids:
                next_id += 1
            p["id"] = next_id
            existing_ids.add(next_id)
            next_id += 1
            changed = True
    if changed:
        _save_profiles(profiles)


def _resolve_profile_key(profiles: dict, name_or_id: str) -> str | None:
    """Resolve -p argument to profile key (name). Returns None if not found."""
    if name_or_id in profiles:
        return name_or_id
    try:
        pid = int(name_or_id)
        for k, p in profiles.items():
            if p.get("id") == pid:
                return k
    except ValueError:
        pass
    return None


def cmd_list() -> None:
    """List all saved tunnel profiles with id, name, target, and link."""
    profiles = _load_profiles()
    _ensure_profile_ids(profiles)
    if not profiles:
        console.print("[dim]No profiles. Use the menu to add one.[/dim]")
        return
    sorted_profiles = sorted(
        profiles.items(),
        key=lambda x: (int(x[1].get("id", 0)), x[1].get("name", x[0])),
    )
    id_width = len(str(len(sorted_profiles)))  # 1–9: width 1; 10+: width 2+
    console.print("[bold]Profiles:[/bold]")
    for k, p in sorted_profiles:
        pid = p.get("id", "?")
        pid_str = str(pid).zfill(id_width) if isinstance(pid, int) else str(pid)
        name = p.get("name", k)
        target = p.get("target", "")
        local_port = int(p.get("local_port", 0))
        protocol = (p.get("protocol") or "http").lower()
        link = f"{protocol}://127.0.0.1:{local_port}" if local_port else ""
        console.print(f"  [cyan]{pid_str}[/cyan]  {name}  [dim]{target}[/dim]  {link}")


def setup_new_profile() -> None:
    """Interactive flow to create a new tunnel profile."""
    console.print("\n[bold]Setup new tunnel profile[/bold]\n")
    target = input("user@host (e.g. user@host.example.com): ").strip()
    if not target:
        console.print("[yellow]Aborted.[/yellow]")
        return
    if "@" not in target:
        import os
        target = f"{os.environ.get('USER', '')}@{target}".strip("@") or target
    remote_port_str = input("Remote host's local port (e.g. 8080): ").strip()
    if not remote_port_str:
        console.print("[yellow]Aborted.[/yellow]")
        return
    try:
        remote_port = int(remote_port_str)
    except ValueError:
        console.print("[red]Invalid port.[/red]")
        return
    local_port_str = input(f"Local port [default: same={remote_port}]: ").strip() or str(remote_port)
    try:
        local_port = int(local_port_str)
    except ValueError:
        console.print("[red]Invalid port.[/red]")
        return
    protocol = input(f"Protocol (http/https) [default: {DEFAULT_PROTOCOL}]: ").strip().lower() or DEFAULT_PROTOCOL
    if protocol not in PROTOCOL_CHOICES:
        protocol = DEFAULT_PROTOCOL
    default_name = _default_profile_name(target, local_port)
    name = input(f"Profile name [default: {default_name}]: ").strip() or default_name
    profiles = _load_profiles()
    if name in profiles:
        console.print(f"[yellow]Profile '{name}' already exists. Choose a different name or edit ~/.pas/xtunnel.json.[/yellow]")
        return
    profiles[name] = {
        "id": _next_profile_id(profiles),
        "target": target,
        "remote_port": remote_port,
        "remote_host": DEFAULT_REMOTE_HOST,
        "local_port": local_port,
        "protocol": protocol,
        "name": name,
    }
    _save_profiles(profiles)
    console.print(f"[green][✓] Profile saved: {name}[/green]\n")


def edit_profile(profile_key: str) -> None:
    """Interactive flow to edit an existing tunnel profile."""
    profiles = _load_profiles()
    if profile_key not in profiles:
        console.print(f"[red]Profile not found: {profile_key}[/red]")
        return
    p = profiles[profile_key]
    console.print(f"\n[bold]Edit profile: {p.get('name', profile_key)}[/bold]\n")
    target = input(f"user@host [{p.get('target', '')}]: ").strip() or p.get("target", "")
    if not target:
        console.print("[yellow]Aborted.[/yellow]")
        return
    if "@" not in target:
        import os
        target = f"{os.environ.get('USER', '')}@{target}".strip("@") or target
    remote_port = p.get("remote_port", 8080)
    remote_port_str = input(f"Remote host's local port [{remote_port}]: ").strip() or str(remote_port)
    try:
        remote_port = int(remote_port_str)
    except ValueError:
        console.print("[red]Invalid port.[/red]")
        return
    local_port = p.get("local_port", remote_port)
    local_port_str = input(f"Local port [{local_port}]: ").strip() or str(local_port)
    try:
        local_port = int(local_port_str)
    except ValueError:
        console.print("[red]Invalid port.[/red]")
        return
    protocol = (p.get("protocol") or DEFAULT_PROTOCOL).lower()
    protocol_in = input(f"Protocol (http/https) [{protocol}]: ").strip().lower() or protocol
    if protocol_in not in PROTOCOL_CHOICES:
        protocol_in = protocol
    default_name = _default_profile_name(target, local_port)
    current_name = p.get("name", profile_key)
    name = input(f"Profile name [{current_name}]: ").strip() or current_name
    profiles = _load_profiles()
    if name != profile_key and name in profiles:
        console.print(f"[yellow]Profile '{name}' already exists. Choose a different name.[/yellow]")
        return
    new_entry = {
        "id": p.get("id", _next_profile_id(profiles)),
        "target": target,
        "remote_port": remote_port,
        "remote_host": p.get("remote_host", DEFAULT_REMOTE_HOST),
        "local_port": local_port,
        "protocol": protocol_in,
        "name": name,
    }
    del profiles[profile_key]
    profiles[name] = new_entry
    _save_profiles(profiles)
    console.print(f"[green][✓] Profile updated: {name}[/green]\n")


def connect_profile(profile_name: str) -> bool:
    """Run xssh tunnel for the given profile. Returns True if started (blocking until tunnel exits)."""
    profiles = _load_profiles()
    if profile_name not in profiles:
        console.print(f"[red]Profile not found: {profile_name}[/red]")
        return False
    p = profiles[profile_name]
    target = p.get("target")
    remote_host = p.get("remote_host", DEFAULT_REMOTE_HOST)
    remote_port = int(p.get("remote_port", 0))
    local_port = int(p.get("local_port", remote_port))
    protocol = (p.get("protocol") or DEFAULT_PROTOCOL).lower()
    if not target or not remote_port:
        console.print("[red]Invalid profile: missing target or remote_port.[/red]")
        return False
    # Invoke xssh -N -L local_port:remote_host:remote_port target
    xssh_path = Path(project_root) / "utils" / "xssh.py"
    cmd = [
        sys.executable,
        str(xssh_path),
        "-N",
        "-L",
        f"{local_port}:{remote_host}:{remote_port}",
        target,
    ]
    link_url = f"{protocol}://127.0.0.1:{local_port}"
    console.print(f"When connected, open: [bold]{link_url}[/bold]\n")
    offer_open = protocol in PROTOCOL_CHOICES
    if offer_open:
        choices = [
            {"title": "Open in browser now (tunnel must be up)", "value": "open"},
            {"title": "Do not open", "value": "no"},
        ]
        formatted = format_menu_choices(choices, title_field="title", value_field="value")
        selected = prompt_toolkit_menu(formatted)
        if selected == "open":
            import webbrowser
            webbrowser.open(link_url)
    console.print("[dim]Starting tunnel...[/dim]\n")
    popen_kw = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if sys.platform != "win32":
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kw)

    def stream_output():
        for line in proc.stdout:
            print(line, end="")

    t = threading.Thread(target=stream_output, daemon=True)
    t.start()
    stop_choices = [choice("q. Stop tunnel and return to menu", "quit")]
    try:
        while True:
            if proc.poll() is not None:
                console.print("[dim]Tunnel exited.[/dim]")
                break
            selected = prompt_toolkit_menu(stop_choices)
            if selected == "quit" or selected is None:
                break
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if sys.platform != "win32":
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform != "win32":
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            proc.kill()
            proc.wait()
    return True


def main():
    parser = argparse.ArgumentParser(
        description="SSH local port-forward profiles (xssh -N -L ...).",
        prog="xtunnel",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["list"],
        help="list: show all saved profiles with id",
    )
    parser.add_argument(
        "-p", "--profile",
        metavar="NAME_OR_ID",
        help="Start tunnel for profile (by name or numeric id)",
    )
    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
        return

    if args.profile is not None:
        profiles = _load_profiles()
        _ensure_profile_ids(profiles)
        key = _resolve_profile_key(profiles, args.profile)
        if key is None:
            console.print(f"[red]Profile not found: {args.profile}[/red]")
            sys.exit(1)
        show_summary()
        connect_profile(key)
        return

    show_summary()
    while True:
        profiles = _load_profiles()
        _ensure_profile_ids(profiles)
        sorted_profiles = sorted(
            profiles.items(),
            key=lambda x: (int(x[1].get("id", 0)), x[1].get("name", x[0])),
        )
        menu_items = [
            {"title": f"{p.get('name', k)} ({p.get('id', '?')})", "value": k}
            for k, p in sorted_profiles
        ]
        menu_items.append({"title": "Setup new tunnel profile", "value": "setup"})
        menu_items.append({"title": "Edit profile", "value": "edit"})
        menu_items.append({"title": "[Quit]", "value": "quit"})
        formatted = format_menu_choices(menu_items, title_field="title", value_field="value")
        console.print("\n[bold]Choose an option:[/bold]")
        choice = prompt_toolkit_menu(formatted)
        if not choice or choice == "quit":
            break
        if choice == "setup":
            setup_new_profile()
            continue
        if choice == "edit":
            if not profiles:
                console.print("[yellow]No profiles yet. Setup one first.[/yellow]")
                continue
            edit_items = [
                {"title": f"{p.get('name', k)} ({p.get('id', '?')})", "value": k}
                for k, p in sorted(profiles.items(), key=lambda x: (int(x[1].get("id", 0)), x[1].get("name", x[0])))
            ]
            edit_items.append({"title": "[Back]", "value": "back"})
            edit_formatted = format_menu_choices(edit_items, title_field="title", value_field="value")
            console.print("\n[bold]Select profile to edit:[/bold]")
            selected = prompt_toolkit_menu(edit_formatted)
            if selected and selected != "back":
                edit_profile(selected)
            continue
        if choice in profiles:
            connect_profile(choice)
            continue
    console.print("[dim]Bye.[/dim]")


if __name__ == "__main__":
    main()
