#!/usr/bin/env python3
"""
@pas-executable
General RTSP stream serving via VLC: manage profiles (one stream per profile) and control playback.
Usage: rtsp-ops [list] | rtsp-ops -p <profile_name>
"""

import argparse
import shutil
import socket
import subprocess
import sys
import time
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
RTSP_OPS_CONFIG_SERVICE = "rtsp-ops"
DEFAULT_PORT = 8554
DEFAULT_RC_PORT = 4212
DEFAULT_PATH = "/stream"
VLC_MACOS_PATH = "/Applications/VLC.app/Contents/MacOS/VLC"
# ---------------------


def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]rtsp-ops[/bold cyan] serves RTSP streams from video files using VLC.\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Profiles:[/bold] One stream per profile (name, video path, port, path).\n"
        "• [bold]Start stream:[/bold] Pick a profile to start VLC; control menu offers play/pause, seek, quit.\n"
        "• [bold]Requires:[/bold] VLC installed (PATH or macOS Applications)."
    )
    console.print(Panel(summary, title="RTSP Stream Profiles", expand=False))


def find_vlc() -> str | None:
    """Find VLC executable path."""
    vlc_path = shutil.which("vlc")
    if vlc_path:
        return vlc_path
    if Path(VLC_MACOS_PATH).exists():
        return VLC_MACOS_PATH
    return None


def send_vlc_command(rc_port: int, command: str) -> bool:
    """Send a command to VLC's RC interface."""
    try:
        with socket.create_connection(("localhost", rc_port), timeout=0.5) as sock:
            sock.sendall(f"{command}\n".encode())
            time.sleep(0.05)
            return True
    except (ConnectionRefusedError, socket.timeout):
        return False


def stop_process(proc: subprocess.Popen) -> None:
    """Terminate a single stream process."""
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except Exception:
        pass


def _load_profiles() -> dict:
    config = load_pas_config(RTSP_OPS_CONFIG_SERVICE)
    return config.get("profiles", {})


def _save_profiles(profiles: dict) -> None:
    config = load_pas_config(RTSP_OPS_CONFIG_SERVICE)
    config["profiles"] = profiles
    save_pas_config(RTSP_OPS_CONFIG_SERVICE, config)


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


def _next_rc_port(profiles: dict) -> int:
    """Return a free RC port for a new profile (above DEFAULT_RC_PORT)."""
    used = {int(p.get("rc_port", 0)) for p in profiles.values() if p.get("rc_port")}
    port = DEFAULT_RC_PORT
    while port in used:
        port += 1
    return port


def setup_new_profile() -> None:
    """Interactive flow to create a new stream profile."""
    console.print("\n[bold]Setup new stream profile[/bold]\n")
    name = input("Profile name: ").strip()
    if not name:
        console.print("[yellow]Aborted.[/yellow]")
        return
    profiles = _load_profiles()
    if name in profiles:
        console.print(f"[yellow]Profile '{name}' already exists.[/yellow]")
        return
    video_path = input("Video file path: ").strip()
    if not video_path:
        console.print("[yellow]Aborted.[/yellow]")
        return
    path_expanded = str(Path(video_path).expanduser().resolve())
    if not Path(path_expanded).exists():
        console.print(f"[red]File not found: {path_expanded}[/red]")
        return
    port_str = input(f"RTSP port [{DEFAULT_PORT}]: ").strip() or str(DEFAULT_PORT)
    try:
        port = int(port_str)
    except ValueError:
        console.print("[red]Invalid port.[/red]")
        return
    path_suffix = input(f"RTSP path (e.g. /stream) [{DEFAULT_PATH}]: ").strip() or DEFAULT_PATH
    if not path_suffix.startswith("/"):
        path_suffix = "/" + path_suffix
    rc_port = _next_rc_port(profiles)
    rc_port_str = input(f"VLC RC port [{rc_port}]: ").strip() or str(rc_port)
    try:
        rc_port = int(rc_port_str)
    except ValueError:
        rc_port = _next_rc_port(profiles)
    profiles[name] = {
        "id": _next_profile_id(profiles),
        "name": name,
        "video_path": path_expanded,
        "port": port,
        "path": path_suffix,
        "rc_port": rc_port,
    }
    _save_profiles(profiles)
    console.print(f"[green][✓] Profile saved: {name}[/green]\n")


def edit_profile(profile_key: str) -> None:
    """Interactive flow to edit an existing stream profile."""
    profiles = _load_profiles()
    if profile_key not in profiles:
        console.print(f"[red]Profile not found: {profile_key}[/red]")
        return
    p = profiles[profile_key]
    console.print(f"\n[bold]Edit profile: {p.get('name', profile_key)}[/bold]\n")
    name = input(f"Profile name [{p.get('name', profile_key)}]: ").strip() or p.get("name", profile_key)
    video_path = input(f"Video file path [{p.get('video_path', '')}]: ").strip() or p.get("video_path", "")
    if video_path:
        path_expanded = str(Path(video_path).expanduser().resolve())
        if not Path(path_expanded).exists():
            console.print(f"[yellow]File not found: {path_expanded}[/yellow]")
        else:
            video_path = path_expanded
    port_str = input(f"RTSP port [{p.get('port', DEFAULT_PORT)}]: ").strip() or str(p.get("port", DEFAULT_PORT))
    try:
        port = int(port_str)
    except ValueError:
        port = p.get("port", DEFAULT_PORT)
    path_suffix = input(f"RTSP path [{p.get('path', DEFAULT_PATH)}]: ").strip() or p.get("path", DEFAULT_PATH)
    if not path_suffix.startswith("/"):
        path_suffix = "/" + path_suffix
    rc_port_str = input(f"VLC RC port [{p.get('rc_port', DEFAULT_RC_PORT)}]: ").strip() or str(p.get("rc_port", DEFAULT_RC_PORT))
    try:
        rc_port = int(rc_port_str)
    except ValueError:
        rc_port = p.get("rc_port", DEFAULT_RC_PORT)
    new_entry = {
        "id": p.get("id", _next_profile_id(profiles)),
        "name": name,
        "video_path": video_path,
        "port": port,
        "path": path_suffix,
        "rc_port": rc_port,
    }
    del profiles[profile_key]
    profiles[name] = new_entry
    _save_profiles(profiles)
    console.print(f"[green][✓] Profile updated: {name}[/green]\n")


def start_stream(vlc_cmd: str, profile_key: str) -> subprocess.Popen | None:
    """Start a single VLC stream for the given profile. Returns process or None."""
    profiles = _load_profiles()
    if profile_key not in profiles:
        return None
    p = profiles[profile_key]
    video_path = Path(p.get("video_path", ""))
    if not video_path.exists():
        console.print(f"[red]Video file not found: {video_path}[/red]")
        return None
    port = int(p.get("port", DEFAULT_PORT))
    path_suffix = p.get("path", DEFAULT_PATH)
    rc_port = int(p.get("rc_port", DEFAULT_RC_PORT))
    name = p.get("name", profile_key)
    cmd = [
        vlc_cmd,
        "--intf", "dummy",
        "--extraintf", "rc",
        "--rc-host", f"localhost:{rc_port}",
        "--sout", f"#rtp{{sdp=rtsp://:{port}{path_suffix}}}",
        "--sout-keep",
        "--repeat",
        str(video_path),
    ]
    console.print(f"[dim]Starting stream [bold]{name}[/bold]: rtsp://localhost:{port}{path_suffix}[/dim]")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def run_stream_control_menu(profile_key: str, proc: subprocess.Popen, rc_port: int) -> None:
    """Menu loop: play/pause, seek, back, quit. One stream only."""
    is_playing = True
    try:
        while True:
            if proc.poll() is not None:
                console.print(f"[red]Stream exited with code {proc.returncode}[/red]")
                break
            state_str = "[green]Playing[/green]" if is_playing else "[yellow]Paused[/yellow]"
            panel_content = f"Stream: [bold]{profile_key}[/bold]  ({state_str})"
            console.print(Panel(panel_content, style="bold cyan"))
            # Shortcuts p, 0-9, b, q (no 01, 02 numbering); questionary stays internal to ppui
            choices = [
                choice("p. Play/Pause", "toggle"),
                choice("0. Seek 0%", "seek_0"),
                choice("1. Seek 10%", "seek_1"),
                choice("2. Seek 20%", "seek_2"),
                choice("3. Seek 30%", "seek_3"),
                choice("4. Seek 40%", "seek_4"),
                choice("5. Seek 50%", "seek_5"),
                choice("6. Seek 60%", "seek_6"),
                choice("7. Seek 70%", "seek_7"),
                choice("8. Seek 80%", "seek_8"),
                choice("9. Seek 90%", "seek_9"),
                choice("b. Back (stop stream)", "back"),
                choice("q. [Quit]", "quit"),
            ]
            selected = prompt_toolkit_menu(choices)
            if selected == "quit" or selected is None:
                break
            if selected == "back":
                break
            if selected == "toggle":
                is_playing = not is_playing
                send_vlc_command(rc_port, "pause")
                console.print(f"[cyan]⏯️  {'Playing' if is_playing else 'Paused'}[/cyan]")
                continue
            if isinstance(selected, str) and selected.startswith("seek_"):
                try:
                    percent = int(selected.split("_")[1]) * 10
                except (IndexError, ValueError):
                    continue
                send_vlc_command(rc_port, f"seek {percent}%")
                if is_playing:
                    send_vlc_command(rc_port, "play")
                console.print(f"[green]▶️  Seek to {percent}%[/green]")
    except KeyboardInterrupt:
        pass
    finally:
        console.print("[dim]Stopping stream...[/dim]")
        stop_process(proc)
        console.print("[green]Done.[/green]\n")


def cmd_list() -> None:
    """List all saved stream profiles with id, name, URL, and video path."""
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
        video = p.get("video_path", "")
        port = p.get("port", DEFAULT_PORT)
        path = p.get("path", DEFAULT_PATH)
        console.print(f"  [cyan]{pid_str}[/cyan]  {name}  [dim]rtsp://localhost:{port}{path}[/dim]  {video}")


def main():
    parser = argparse.ArgumentParser(
        description="RTSP stream serving via VLC (profiles and control menu).",
        prog="rtsp-ops",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["list"],
        help="list: show all saved profiles",
    )
    parser.add_argument(
        "-p", "--profile",
        metavar="NAME_OR_ID",
        help="Start stream for profile (by name or numeric id)",
    )
    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
        return

    if args.profile is not None:
        vlc_cmd = find_vlc()
        if not vlc_cmd:
            console.print("[red]VLC not found. Install VLC or ensure it's in your PATH.[/red]")
            sys.exit(1)
        show_summary()
        profiles = _load_profiles()
        _ensure_profile_ids(profiles)
        key = _resolve_profile_key(profiles, args.profile)
        if key is None:
            console.print(f"[red]Profile not found: {args.profile}[/red]")
            sys.exit(1)
        proc = start_stream(vlc_cmd, key)
        if proc:
            p = profiles[key]
            run_stream_control_menu(key, proc, int(p.get("rc_port", DEFAULT_RC_PORT)))
        return

    vlc_cmd = find_vlc()
    if not vlc_cmd:
        console.print("[red]VLC not found. Install VLC or ensure it's in your PATH.[/red]")
        sys.exit(1)
    show_summary()
    while True:
        profiles = _load_profiles()
        _ensure_profile_ids(profiles)
        sorted_profiles = sorted(
            profiles.items(),
            key=lambda x: (int(x[1].get("id", 0)), x[1].get("name", x[0])),
        )
        menu_items = [
            {"title": f"PROFILE: {p.get('name', k)} ({p.get('id', '?')})", "value": k}
            for k, p in sorted_profiles
        ]
        menu_items.append({"title": "Setup new stream profile", "value": "setup"})
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
            edit_sorted = sorted(
                profiles.items(),
                key=lambda x: (int(x[1].get("id", 0)), x[1].get("name", x[0])),
            )
            edit_items = [
                {"title": f"{p.get('name', k)} ({p.get('id', '?')})", "value": k}
                for k, p in edit_sorted
            ]
            edit_items.append({"title": "[Back]", "value": "back"})
            edit_formatted = format_menu_choices(edit_items, title_field="title", value_field="value")
            console.print("\n[bold]Select profile to edit:[/bold]")
            selected = prompt_toolkit_menu(edit_formatted)
            if selected and selected != "back":
                edit_profile(selected)
            continue
        if choice in profiles:
            proc = start_stream(vlc_cmd, choice)
            if proc:
                run_stream_control_menu(choice, proc, int(profiles[choice].get("rc_port", DEFAULT_RC_PORT)))
            continue
    console.print("[dim]Bye.[/dim]")


if __name__ == "__main__":
    main()
