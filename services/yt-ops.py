#!/usr/bin/env python3
"""
@pas-executable
YouTube Operations Tool for management of Google accounts, channels, and video uploads.
"""

import os
import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
import json
import argparse
import yaml
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Google API Imports
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    print("Missing dependencies. Please run 'pas upgrade' or 'pip install google-api-python-client google-auth-oauthlib google-auth-httplib2'")
    sys.exit(1)

# Add services directory to path for helpers
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import (
    load_pas_config, 
    save_pas_config, 
    prompt_yes_no, 
    run_command, 
    get_pas_config_dir,
    prompt_toolkit_menu,
    format_menu_choices,
    check_gcloud_installed,
    check_gcloud_auth,
    select_gcp_project,
    ensure_gcp_apis_enabled,
    check_gcp_billing
)

console = Console()

# Constants
YT_CONFIG_NAME = "yt-ops"
CHANNELS_FILE = get_pas_config_dir() / "yt-channels.json"
CHANNELS_DIR = get_pas_config_dir() / "yt-channels"
SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "yt-ops"
TOOL_TITLE = "YouTube Operations"
TOOL_SHORT_DESC = "YouTube Operations Tool for accounts, channels, and video uploads."
TOOL_DESCRIPTION = "YouTube Operations Tool. Manages multiple accounts, GCP/YouTube API, channel assets, and uploads."

DEFAULT_META = {
    "title": "TODO",
    "description": "#animalfun #furry #shorts #ytshorts",
    "tags": ["Furry", "Shorts", "Youtube Shorts"],
    "privacyStatus": "private",
    "madeForKids": False,
    "embeddable": True,
    "publicStatsViewable": True,
    "playlistIds": [],
    "playlistTitles": [],
    "language": ""
}

def show_intro():
    info_text = """
[bold]%s[/bold]

- [cyan]GCP Setup[/cyan]: Links to a GCP project and enables YouTube API.
- [cyan]Account Management[/cyan]: Add and switch between multiple YouTube accounts.
- [cyan]Channel Assets[/cyan]: Maintains metadata and assets per channel in ~/.pas/yt-channels/.
- [cyan]Smart Upload[/cyan]: Automatic title extraction and metadata merging.
""" % TOOL_DESCRIPTION
    console.print(Panel(info_text.strip(), title=TOOL_TITLE, border_style="red"))
    console.print("\n")

def ensure_yt_api_enabled(project_id: str):
    """Ensure YouTube Data API v3 is enabled."""
    console.print(f"[cyan]Checking YouTube Data API v3 for project {project_id}...[/cyan]")
    ensure_gcp_apis_enabled(project_id, ["youtube.googleapis.com"])

def setup_gcp_for_youtube():
    """Initial setup for GCP and OAuth credentials."""
    config = load_pas_config(YT_CONFIG_NAME)
    
    if not check_gcloud_installed():
        console.print("[red]gcloud CLI not found.[/red]")
        sys.exit(1)
        
    auth_status = check_gcloud_auth()
    if auth_status is None: # User quit
        sys.exit(0)
    if not auth_status:
        console.print("[red]gcloud CLI setup required.[/red]")
        sys.exit(1)
        
    project_id = config.get("project_id")
    if not project_id:
        project_id = select_gcp_project()
        if not project_id:
            sys.exit(1)
        config["project_id"] = project_id
        save_pas_config(YT_CONFIG_NAME, config)
        
    ensure_yt_api_enabled(project_id)
    check_gcp_billing(project_id)
    
    if "client_id" not in config or "client_secret" not in config:
        console.print("\n[bold yellow]OAuth 2.0 Client Credentials Required[/bold yellow]")
        console.print("1. Go to: [blue]https://console.cloud.google.com/apis/credentials[/blue]")
        console.print("2. Click [bold]+ CREATE CREDENTIALS[/bold] -> [bold]OAuth client ID[/bold]")
        console.print("3. Application type: [bold]Desktop app[/bold]")
        console.print("4. Name: [bold]PAS Toolkit[/bold]")
        console.print("5. Click [bold]CREATE[/bold] and then [bold]DOWNLOAD JSON[/bold]")
        
        json_path = input("\nEnter path to downloaded client_secrets.json: ").strip().strip("'\"")
        path = Path(json_path).expanduser()
        
        if path.exists():
            try:
                data = json.loads(path.read_text())
                # Handle both 'web' and 'installed' types
                creds_type = "installed" if "installed" in data else "web"
                config["client_id"] = data[creds_type]["client_id"]
                config["client_secret"] = data[creds_type]["client_secret"]
                save_pas_config(YT_CONFIG_NAME, config)
                console.print("[green]OAuth credentials saved securely.[/green]")
                
                if prompt_yes_no("Delete the downloaded JSON file?"):
                    path.unlink()
            except Exception as e:
                console.print(f"[red]Error parsing JSON: {e}[/red]")
                sys.exit(1)
        else:
            console.print("[red]File not found. Please try again.[/red]")
            sys.exit(1)
            
    return config

def get_oauth_creds(config: Dict[str, Any]) -> Dict[str, Any]:
    """Format stored credentials for the Google OAuth library."""
    return {
        "installed": {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

def get_authenticated_service(account_name: str):
    """Get an authorized YouTube service instance."""
    config = load_pas_config(YT_CONFIG_NAME)
    accounts = config.get("accounts", {})
    
    if account_name not in accounts:
        console.print(f"[red]Account '{account_name}' not found.[/red]")
        return None
        
    token_data = accounts[account_name].get("token")
    creds = None
    
    if token_data:
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(get_oauth_creds(config), SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save updated token
        accounts[account_name]["token"] = json.loads(creds.to_json())
        config["accounts"] = accounts
        save_pas_config(YT_CONFIG_NAME, config)
        
    return build('youtube', 'v3', credentials=creds)

def add_account():
    """Authorize a new Google account and discover its channels."""
    config = load_pas_config(YT_CONFIG_NAME)
    flow = InstalledAppFlow.from_client_config(get_oauth_creds(config), SCOPES)
    creds = flow.run_local_server(port=0)
    
    youtube = build('youtube', 'v3', credentials=creds)
    request = youtube.channels().list(part="snippet,contentDetails,statistics", mine=True)
    response = request.execute()
    
    if not response.get("items"):
        console.print("[red]No YouTube channels found for this account.[/red]")
        return
        
    accounts = config.get("accounts", {})
    
    for item in response["items"]:
        channel_id = item["id"]
        snippet = item["snippet"]
        handle = snippet.get("customUrl", f"channel_{channel_id}").lstrip("@")
        title = snippet["title"]
        
        console.print(f"[green]Found channel: [bold]{title}[/bold] (@{handle})[/green]")
        
        # Initialize channel directory
        channel_path = CHANNELS_DIR / handle
        channel_path.mkdir(parents=True, exist_ok=True)
        
        meta_file = channel_path / "meta.yaml"
        if not meta_file.exists():
            with open(meta_file, "w") as f:
                yaml.dump(DEFAULT_META, f, sort_keys=False)
                
        accounts[handle] = {
            "channel_id": channel_id,
            "title": title,
            "handle": handle,
            "token": json.loads(creds.to_json())
        }
        
    config["accounts"] = accounts
    save_pas_config(YT_CONFIG_NAME, config)
    console.print("[bold green]Account and channels updated successfully![/bold green]")

def list_accounts():
    """Display a table of managed YouTube channels."""
    config = load_pas_config(YT_CONFIG_NAME)
    accounts = config.get("accounts", {})
    
    if not accounts:
        console.print("[yellow]No accounts registered yet. Use 'yt-ops --add' to add one.[/yellow]")
        return
        
    table = Table(title="Managed YouTube Channels")
    table.add_column("Handle", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Channel ID", style="dim")
    
    for handle, data in accounts.items():
        table.add_row(f"@{handle}", data["title"], data["channel_id"])
        
    console.print(table)

def load_merged_metadata(handle: str, video_path: Path) -> Dict[str, Any]:
    """Load and merge metadata from multiple sources with precedence."""
    merged = DEFAULT_META.copy()
    
    # 1. Global channel-specific meta
    channel_meta_path = CHANNELS_DIR / handle / "meta.yaml"
    if channel_meta_path.exists():
        with open(channel_meta_path, "r") as f:
            channel_meta = yaml.safe_load(f) or {}
            merged.update(channel_meta)
            
    # 2. Local directory meta
    local_meta_path = Path("meta.yaml")
    if local_meta_path.exists():
        with open(local_meta_path, "r") as f:
            local_meta = yaml.safe_load(f) or {}
            merged.update(local_meta)
            
    # 3. Automatic title from filename if title is missing, empty, or "TODO"
    title = merged.get("title")
    if not title or title.strip() == "" or title == "TODO":
        merged["title"] = video_path.stem.replace("_", " ").replace("-", " ").title()
        
    return merged

def upload_video(account_name: str, video_path: Path):
    """Perform a resumable upload of a video to YouTube."""
    # Ensure we have an absolute path and handle potential shell quoting issues
    video_path = Path(str(video_path).strip("'\" ")).expanduser().resolve()
    
    if not video_path.exists():
        console.print(f"[red]Video file not found: {video_path}[/red]")
        return

    youtube = get_authenticated_service(account_name)
    if not youtube:
        return

    meta = load_merged_metadata(account_name, video_path)
    
    console.print(f"\n[bold]Preparing upload for account: [cyan]@{account_name}[/cyan][/bold]")
    console.print(f"File: [yellow]{video_path}[/yellow]")
    console.print(f"Title: [bold]{meta['title']}[/bold]")
    console.print(f"Privacy: [bold]{meta['privacyStatus']}[/bold]")

    body = {
        'snippet': {
            'title': meta['title'],
            'description': meta['description'],
            'tags': meta['tags'],
            'categoryId': meta.get('categoryId', '22') # Default to People & Blogs
        },
        'status': {
            'privacyStatus': meta['privacyStatus'],
            'selfDeclaredMadeForKids': meta['madeForKids'],
            'embeddable': meta['embeddable'],
            'publicStatsViewable': meta['publicStatsViewable']
        }
    }

    if meta.get('language'):
        body['snippet']['defaultLanguage'] = meta['language']
        body['snippet']['defaultAudioLanguage'] = meta['language']

    media = MediaFileUpload(
        str(video_path), 
        mimetype='application/octet-stream', 
        resumable=True
    )

    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )

    console.print("\n[bold green]Uploading...[/bold green]")
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                console.print(f"Uploaded {int(status.progress() * 100)}%", end="\r")
        
        console.print(f"\n[bold green]Success![/bold green] Video uploaded. ID: [cyan]{response['id']}[/cyan]")
        console.print(f"URL: [blue]https://youtu.be/{response['id']}[/blue]")
        
        # Add to playlists if specified
        for playlist_id in meta.get('playlistIds', []):
            try:
                youtube.playlistItems().insert(
                    part='snippet',
                    body={
                        'snippet': {
                            'playlistId': playlist_id,
                            'resourceId': {
                                'kind': 'youtube#video',
                                'videoId': response['id']
                            }
                        }
                    }
                ).execute()
                console.print(f"[green]Added to playlist: {playlist_id}[/green]")
            except Exception as pe:
                console.print(f"[yellow]Failed to add to playlist {playlist_id}: {pe}[/yellow]")

    except HttpError as e:
        console.print(f"\n[red]An HTTP error occurred: {e.resp.status} {e.content}[/red]")
    except Exception as e:
        console.print(f"\n[red]An error occurred: {e}[/red]")

def main():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("--add", action="store_true", help="Add a new YouTube account")
    parser.add_argument("--list", action="store_true", help="List registered accounts")
    parser.add_argument("--upload", help="Path to video file for upload")
    parser.add_argument("--account", help="Handle of the account to use for upload")
    args = parser.parse_args()
    
    show_intro()
    
    # Outer loop to allow "Back" from the main menu to return to GCP/Auth setup
    while True:
        config = setup_gcp_for_youtube()
        
        if args.add:
            while True:
                add_account()
                if not prompt_yes_no("Would you like to authorize another YouTube account?"):
                    break
            return

        if args.list:
            list_accounts()
            return

        if args.upload:
            accounts = config.get("accounts", {})
            if not accounts:
                console.print("[yellow]No accounts found. Please add one first.[/yellow]")
                return
            video_path = Path(args.upload).expanduser().resolve()
            selected_account = args.account
            if not selected_account:
                if len(accounts) == 1:
                    selected_account = list(accounts.keys())[0]
                else:
                    choices = format_menu_choices(list(accounts.keys()))
                    console.print("\n[bold cyan]Select an account for upload:[/bold cyan]")
                    selected_account = prompt_toolkit_menu(choices)
            if selected_account:
                upload_video(selected_account, video_path)
            return

        # Interactive Loop
        restart_setup = False
        while True:
            config = load_pas_config(YT_CONFIG_NAME)
            accounts = config.get("accounts", {})
            
            list_accounts()
            
            menu_options = []
            for handle in accounts.keys():
                menu_options.append({"title": f"Use channel: @{handle}", "value": ("use", handle)})
            
            menu_options.append({"title": "Add new YouTube account", "value": ("add", None)})
            menu_options.append({"title": "[Back]", "value": ("back", None)})
            menu_options.append({"title": "[Quit]", "value": ("quit", None)})

            choices = format_menu_choices(menu_options, title_field="title", value_field="value")
            console.print("\n[bold cyan]YouTube Operations Menu:[/bold cyan]")
            result = prompt_toolkit_menu(choices)
            
            if not result or result[0] == "quit":
                return
            
            if result[0] == "back":
                restart_setup = True
                break
                
            action, handle = result
            
            if action == "add":
                add_account()
            elif action == "use":
                # Nested menu for channel operations
                exit_requested = False
                while True:
                    console.print(f"\n[bold cyan]Channel @{handle} Operations:[/bold cyan]")
                    sub_options = [
                        {"title": "Upload a video", "value": "upload"},
                        {"title": "View metadata template", "value": "meta"},
                        {"title": "[Back]", "value": "back"},
                        {"title": "[Quit]", "value": "quit"}
                    ]
                    sub_choices = format_menu_choices(sub_options, title_field="title", value_field="value")
                    sub_action = prompt_toolkit_menu(sub_choices)
                    
                    if not sub_action or sub_action == "back":
                        break
                    if sub_action == "quit":
                        exit_requested = True
                        break
                    
                    if sub_action == "upload":
                        video_files = []
                        for ext in [".mp4", ".mov", ".mkv", ".avi", ".webm"]:
                            video_files.extend(list(Path(".").glob(f"*{ext}")))
                            video_files.extend(list(Path(".").glob(f"*{ext.upper()}")))
                        
                        video_files = sorted(list(set(video_files)))
                        
                        selected_video = None
                        if video_files:
                            console.print("\n[bold cyan]Video files in current directory:[/bold cyan]")
                            file_options = [{"title": str(f), "value": f} for f in video_files]
                            file_options.append({"title": "[Enter path manually]", "value": "__manual__"})
                            file_options.append({"title": "[Back]", "value": "__back__"})
                            file_options.append({"title": "[Quit]", "value": "__quit__"})
                            
                            file_choices = format_menu_choices(file_options, title_field="title", value_field="value")
                            selected_video = prompt_toolkit_menu(file_choices)
                            
                            if selected_video == "__quit__":
                                exit_requested = True
                                break
                            if selected_video == "__back__":
                                continue
                            elif selected_video == "__manual__":
                                selected_video = None
                        
                        if exit_requested:
                            break

                        if not selected_video:
                            video_input = input("\nEnter path to video file (or drag and drop): ").strip().strip("'\"")
                            if video_input:
                                selected_video = Path(video_input).expanduser().resolve()
                        
                        if selected_video:
                            upload_video(handle, Path(selected_video))
                    elif sub_action == "meta":
                        meta_path = CHANNELS_DIR / handle / "meta.yaml"
                        console.print(f"\n[bold]Metadata for @{handle}:[/bold]")
                        console.print(f"File: {meta_path}")
                        if meta_path.exists():
                            console.print(Panel(meta_path.read_text(), border_style="dim"))
                        else:
                            console.print("[yellow]Meta file not found.[/yellow]")
                        input("\nPress Enter to continue...")
                
                if exit_requested:
                    return

        if not restart_setup:
            break

if __name__ == "__main__":
    main()
