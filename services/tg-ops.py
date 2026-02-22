#!/usr/bin/env python3
"""
@pas-executable
Telegram bot management tool for configuration and webhook operations.
"""

import argparse
import os
import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List

from rich.console import Console
from rich.panel import Panel

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import load_pas_config, save_pas_config, prompt_yes_no

console = Console()

# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "tg-ops"
TOOL_TITLE = "Telegram Bot Operations"
TOOL_SHORT_DESC = "Telegram bot management for configuration and webhook operations."
TOOL_DESCRIPTION = "Telegram bot management tool for configuration and webhook operations. Multi-bot, webhook ops, live status."

def tg_api_request(token: str, method: str, data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Make a request to the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    
    encoded_data = None
    if data:
        encoded_data = json.dumps(data).encode("utf-8")
        
    req = urllib.request.Request(url, data=encoded_data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        error_resp = e.read().decode()
        try:
            error_data = json.loads(error_resp)
            print(f"Telegram API Error: {error_data.get('description', 'Unknown error')}")
        except json.JSONDecodeError:
            print(f"API Error ({e.code}): {error_resp}")
    except Exception as e:
        print(f"Request Error: {e}")
    return None

def validate_token(token: str) -> Optional[Dict[str, Any]]:
    """Check if the token is valid and return bot info."""
    print(f"Validating token...")
    resp = tg_api_request(token, "getMe")
    if resp and resp.get("ok"):
        return resp.get("result")
    return None

def select_bot() -> Optional[str]:
    """Select a bot from the saved config or add a new one."""
    config = load_pas_config("tg")
    bots = config.get("bots", [])
    
    while True:
        if bots:
            print("\n--- Saved Telegram Bots ---")
            for idx, bot in enumerate(bots, 1):
                print(f"{idx}. {bot['first_name']} (@{bot['username']})")
            print(f"{len(bots) + 1}. Add a new bot token")
            print("q. Quit")
            
            choice = input(f"\nSelect a bot (1-{len(bots) + 1}) or 'q': ").strip().lower()
            if choice == 'q':
                return None
            
            try:
                sel = int(choice)
                if 1 <= sel <= len(bots):
                    return bots[sel-1]['token']
                elif sel == len(bots) + 1:
                    pass # Fall through to add new token
                else:
                    print("Invalid selection.")
                    continue
            except ValueError:
                print("Please enter a number or 'q'.")
                continue
        
        # Add new token
        token = input("\nEnter your Telegram bot token (from BotFather): ").strip()
        if not token:
            if bots: continue
            return None
            
        bot_info = validate_token(token)
        if bot_info:
            bot_entry = {
                "token": token,
                "id": bot_info["id"],
                "first_name": bot_info["first_name"],
                "username": bot_info["username"]
            }
            # Avoid duplicates
            bots = [b for b in bots if b["id"] != bot_info["id"]]
            bots.append(bot_entry)
            config["bots"] = bots
            save_pas_config("tg", config)
            print(f"Added {bot_info['first_name']} (@{bot_info['username']})")
            return token
        else:
            print("Invalid token. Please try again.")
            if not bots: return None

def show_status(token: str):
    """Show bot status and webhook info."""
    print("\n--- Bot Status ---")
    me = tg_api_request(token, "getMe")
    if me and me.get("ok"):
        res = me["result"]
        print(f"Name:     {res['first_name']}")
        print(f"Username: @{res['username']}")
        print(f"ID:       {res['id']}")
    
    webhook = tg_api_request(token, "getWebhookInfo")
    if webhook and webhook.get("ok"):
        res = webhook["result"]
        url = res.get("url")
        if url:
            print("\n--- Webhook Configuration ---")
            print(f"URL:            {url}")
            print(f"Pending Update Count: {res.get('pending_update_count', 0)}")
            if res.get("last_error_date"):
                import datetime
                err_date = datetime.datetime.fromtimestamp(res["last_error_date"])
                print(f"Last Error Date:      {err_date}")
                print(f"Last Error Message:   {res.get('last_error_message', 'None')}")
        else:
            print("\nStatus: Long Polling (No Webhook Set)")

def configure_webhook(token: str):
    """Set a new webhook URL."""
    url = input("\nEnter the webhook URL (must be HTTPS): ").strip()
    if not url:
        print("Cancelled.")
        return
    
    if not url.startswith("https://"):
        print("Error: Webhook URL must start with https://")
        return
        
    print(f"Setting webhook to: {url}...")
    resp = tg_api_request(token, "setWebhook", {"url": url})
    if resp and resp.get("ok"):
        print("Success: Webhook updated.")
        show_status(token)
    else:
        print("Failed to set webhook.")

def remove_webhook(token: str):
    """Remove the current webhook."""
    if not prompt_yes_no("Are you sure you want to remove the webhook?", default=False):
        return
        
    print("Deleting webhook...")
    resp = tg_api_request(token, "deleteWebhook")
    if resp and resp.get("ok"):
        print("Success: Webhook removed. Bot is now in long-polling mode.")
        show_status(token)
    else:
        print("Failed to remove webhook.")

def main():
    parser = argparse.ArgumentParser(
        description=TOOL_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    args = parser.parse_args()

    info_text = """
[bold]%s[/bold]

- [cyan]Multi-Bot[/cyan]: Switch between different bots stored in your config.
- [cyan]Webhook Ops[/cyan]: Easily set, remove, or check your bot's webhook URL.
- [cyan]Live Status[/cyan]: Real-time retrieval of bot info and pending update counts.
""" % TOOL_DESCRIPTION
    console.print(Panel(info_text.strip(), title=TOOL_TITLE, border_style="blue"))
    console.print("\n")

    token = select_bot()
    if not token:
        print("No bot selected. Exiting.")
        sys.exit(0)

    while True:
        show_status(token)
        print("\nActions:")
        print("1. Set Webhook")
        print("2. Remove Webhook")
        print("r. Refresh Status")
        print("b. Switch Bot (Back)")
        print("q. Quit")
        
        choice = input("\nSelect an action: ").strip().lower()
        if choice == 'q':
            break
        elif choice == 'b':
            token = select_bot()
            if not token: break
        elif choice == 'r':
            continue
        elif choice == '1':
            configure_webhook(token)
        elif choice == '2':
            remove_webhook(token)
        else:
            print("Invalid selection.")

if __name__ == "__main__":
    main()

