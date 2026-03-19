#!/usr/bin/env python3
"""
@pas-executable
Central administrative tool for PAS. Controls updates, listing tools, and AI help.
Subcommands:
- list: Scans the repository for tools and shows their descriptions.
- ask: Query an AI assistant for help or finding tools.
- upgrade: Pulls latest code and refreshes system environment/symlinks.
- up: Alias for upgrade.
- repo: Opens the official GitHub repository in your browser.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel

import questionary

from helpers.core import load_pas_config, save_pas_config, format_menu_choices, prompt_toolkit_menu, Menu

console = Console()

def check_platform():
    """Check if the current platform is macOS and warn if not."""
    if sys.platform != "darwin":
        console.print(Panel(
            "[bold yellow]Warning:[/bold yellow] PAS Toolkit is primarily designed for [bold]macOS[/bold].\n"
            f"You are running on [bold cyan]{sys.platform}[/bold cyan]. Some features (Keychain, VNC, etc.) may not work as expected.",
            title="Platform Compatibility",
            border_style="yellow"
        ))

def get_pas_root() -> Path:
    """Find the root of the PAS installation."""
    # Assume the script is in [PAS_ROOT]/services/
    return Path(__file__).resolve().parent.parent

def run_command(cmd: list[str], cwd: Path) -> bool:
    """Run a shell command and print output."""
    print(f"\n> {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: Command failed with exit code {e.returncode}")
        return False

def get_pas_version() -> str:
    """Read the version from the VERSION file."""
    version_path = get_pas_root() / "VERSION"
    if version_path.exists():
        return version_path.read_text().strip()
    return "unknown"

def migrate_legacy_llm_config():
    """Migrate legacy LLM config (~/.pas/llms.json) to new format (~/.pas/ai-models.json + ~/.pas/pas.json)."""
    legacy_path = Path.home() / ".pas" / "llms.json"
    if not legacy_path.exists():
        return

    console.print("[yellow]Legacy LLM configuration found. Migrating to new format...[/yellow]")
    
    try:
        # 1. Load legacy config
        legacy_config = load_pas_config("llms")
        if not legacy_config:
            return

        # 2. Prepare new shared config (ai-models.json)
        ai_models_config = load_pas_config("ai-models")
        if "profiles" not in ai_models_config:
            ai_models_config["profiles"] = {}
        if "configs" not in ai_models_config:
            ai_models_config["configs"] = {}

        legacy_providers = legacy_config.get("providers", {})
        legacy_configs = legacy_config.get("configs", {})
        legacy_active_id = legacy_config.get("active_config_id")

        # Migrate providers to profiles
        for p_id, p_data in legacy_providers.items():
            if isinstance(p_data, dict) and p_id not in ai_models_config["profiles"]:
                token = p_data.get("token")
                if token:
                    ai_models_config["profiles"][p_id] = {
                        "provider": p_id,
                        "token": token
                    }

        # Migrate configs
        for c_id, c_data in legacy_configs.items():
            if isinstance(c_data, dict) and c_id not in ai_models_config["configs"]:
                provider = c_data.get("provider")
                model = c_data.get("model")
                if provider and model:
                    ai_models_config["configs"][c_id] = {
                        "profile": provider,
                        "model": model
                    }

        save_pas_config("ai-models", ai_models_config)

        # 3. Migrate active choice to pas.json
        if legacy_active_id:
            pas_config = load_pas_config("pas")
            if not pas_config.get("active_ai_config_id"):
                pas_config["active_ai_config_id"] = legacy_active_id
                save_pas_config("pas", pas_config)

        # 4. Backup legacy file
        bak_path = legacy_path.with_suffix(".json.bak")
        os.rename(legacy_path, bak_path)
        console.print(f"[green]Migration complete! Legacy config backed up to {bak_path}[/green]")
        
    except Exception as e:
        console.print(f"[red]Error during migration: {e}[/red]")

def cmd_upgrade(args):
    """Update PAS from the remote repository and refresh setup."""
    root = get_pas_root()
    old_version = get_pas_version()
    print(f"Upgrading PAS at {root} (Current version: {old_version})...")
    
    # 1. git pull
    if not run_command(["git", "pull"], cwd=root):
        sys.exit(1)
        
    # 2. make setup
    if not run_command(["make", "setup"], cwd=root):
        sys.exit(1)
        
    # 3. Run migrations
    migrate_legacy_llm_config()

    new_version = get_pas_version()
    console.print(f"\n[bold green]PAS upgrade complete![/bold green]")
    if old_version != new_version:
        console.print(f"Updated from [yellow]{old_version}[/yellow] to [bold cyan]{new_version}[/bold cyan]")
    else:
        console.print(f"Already at latest version: [bold cyan]{new_version}[/bold cyan]")
    console.print("\n[bold yellow]Note:[/bold yellow] If new tools are not appearing in tab-completion, run: [cyan]rehash[/cyan]")

def cmd_repo(args):
    """Open the official PAS GitHub repository."""
    url = "https://github.com/nextoken/pas"
    console.print(f"\nOfficial Repository: [bold cyan]{url}[/bold cyan]")
    console.print("Opening in default browser...\n")
    webbrowser.open(url)

def cmd_ask(args):
    """Ask an AI assistant about available tools."""
    ai_models_config = load_pas_config("ai-models")
    pas_config = load_pas_config("pas")
    
    active_config_id = pas_config.get("active_ai_config_id")
    
    if not active_config_id:
        # Fallback to system default if any, or trigger setup
        active_config_id = ai_models_config.get("active_config_id")
        if not active_config_id:
            setup_llm(ai_models_config)
            pas_config = load_pas_config("pas")
            active_config_id = pas_config.get("active_ai_config_id")
    
    active_config = ai_models_config.get("configs", {}).get(active_config_id)
    
    if not active_config:
        setup_llm(ai_models_config)
        pas_config = load_pas_config("pas")
        active_config_id = pas_config.get("active_ai_config_id")
        active_config = ai_models_config.get("configs", {}).get(active_config_id)

    if not active_config:
        console.print("[red]Error: Could not determine active AI configuration.[/red]")
        return

    profile_id = active_config.get("profile")
    model = active_config.get("model")
    
    profile_data = ai_models_config.get("profiles", {}).get(profile_id, {})
    provider = profile_data.get("provider")
    provider_token = profile_data.get("token")
    
    if not provider or not provider_token:
        console.print(f"[red]Error: Profile '{profile_id}' is missing provider or token.[/red]")
        setup_llm(ai_models_config)
        return

    provider_config = {
        "token": provider_token,
        "model": model
    }
    
    # Validate token and get metadata
    is_valid, token_meta = validate_llm_token(provider, provider_token)
    if not provider_token or not is_valid:
        if provider_token:
            console.print(f"[bold red]Error:[/bold red] {provider} API token for profile '{profile_id}' is invalid or expired.")
        setup_llm(ai_models_config)
        # Reload everything after setup
        ai_models_config = load_pas_config("ai-models")
        pas_config = load_pas_config("pas")
        active_config_id = pas_config.get("active_ai_config_id")
        active_config = ai_models_config.get("configs", {}).get(active_config_id, {})
        profile_id = active_config.get("profile")
        model = active_config.get("model")
        profile_data = ai_models_config.get("profiles", {}).get(profile_id, {})
        provider = profile_data.get("provider")
        provider_token = profile_data.get("token")
        provider_config = {"token": provider_token, "model": model}
        is_valid, token_meta = validate_llm_token(provider, provider_token)

    initial_query = " ".join(args.query).strip() if args.query else None
    is_interactive = not initial_query
    
    while True:
        query = initial_query
        if is_interactive:
            # Refresh config in case it was changed in the loop
            ai_models_config = load_pas_config("ai-models")
            pas_config = load_pas_config("pas")
            active_config_id = pas_config.get("active_ai_config_id", "Not configured")
            active_config = ai_models_config.get("configs", {}).get(active_config_id, {})
            profile_id = active_config.get("profile", "N/A")
            model = active_config.get("model", "N/A")
            
            profile_data = ai_models_config.get("profiles", {}).get(profile_id, {})
            provider = profile_data.get("provider", "N/A")
            provider_token = profile_data.get("token")
            provider_config = {"token": provider_token, "model": model}
            
            # Interactive mode
            menu_choices = [
                {"title": "Ask a question", "value": "ASK"},
                {"title": f"Switch/Setup AI Config (Current: {active_config_id})", "value": "SETUP"},
                {"title": "[Quit]", "value": "QUIT"}
            ]
            formatted_choices = format_menu_choices(menu_choices, title_field="title", value_field="value")
            action = prompt_toolkit_menu(formatted_choices)
            
            if action == "QUIT" or not action:
                return
            elif action == "SETUP":
                setup_llm(ai_models_config)
                continue
            
            query = questionary.text("Enter your question:").ask()
            if not query:
                continue

        # Re-validate token and get metadata for current provider
        _, token_meta = validate_llm_token(provider, provider_token)

        # Prepare tools info
        tools = get_tools_info()
        tools_context = "\n".join([f"- {name}: {desc}" for name, desc in tools])
        
        # Get token age for display
        from helpers.core import get_secret_age, SECRET_ROTATION_DAYS
        token_age = get_secret_age("ai-models", f"profiles.{profile_id}.token")
        
        # Try to get actual expiration from OpenRouter metadata
        actual_expiry_days = None
        if token_meta and "expires_at" in token_meta and token_meta["expires_at"]:
            try:
                expiry_str = token_meta["expires_at"].replace("Z", "+00:00")
                expiry_date = datetime.datetime.fromisoformat(expiry_str)
                now = datetime.datetime.now(datetime.timezone.utc)
                actual_expiry_days = (expiry_date - now).days
            except Exception:
                pass

        if actual_expiry_days is not None:
            expiry_str = f", Actual Expiry in: {actual_expiry_days} days" if actual_expiry_days > 0 else ", [bold red]EXPIRED[/bold red]"
            age_str = f" (Token age: {token_age} days{expiry_str})"
        elif token_age is not None:
            expires_in = SECRET_ROTATION_DAYS - token_age
            expiry_str = f", Policy Expiry in: {expires_in} days" if expires_in > 0 else ", [bold red]EXPIRED[/bold red]"
            age_str = f" (Token age: {token_age} days{expiry_str})"
        else:
            age_str = ""

        root = get_pas_root()
        readme_content = ""
        readme_path = root / "README.md"
        if readme_path.exists():
            try:
                readme_content = readme_path.read_text(errors='ignore')
            except Exception:
                pass

        dev_guide_content = ""
        dev_guide_path = root / "dev-guide.md"
        if dev_guide_path.exists():
            try:
                dev_guide_content = dev_guide_path.read_text(errors='ignore')
            except Exception:
                pass

        project_url = "https://github.com/nextoken/pas"
        author = "Nextoken (https://github.com/nextoken)"
        
        system_prompt = f"""You are an assistant for the PAS (Process Automation Setups) toolkit. 
Your goal is to help users find the right tool for their task and answer questions about the toolkit itself.

[Project Info]
- Project Name: PAS (Process Automation Setups)
- Official Repository: {project_url}
- Author/Organization: {author}
- Description: A collection of automation tools for developers and power users.

[README Overview]
{readme_content}

[Developer Guide]
{dev_guide_content}

[Available Tools]
Below is a list of available tools and their detailed descriptions (extracted from their help/docstrings):
{tools_context}

[Instructions]
1. When asked about a task, be concise and recommend specific tool names from the list.
2. Use the [Available Tools] section to explain what a tool does and what subcommands or features it has.
3. If asked about the author or project URL, use the [Project Info] provided above.
4. Use the [README Overview] to explain the toolkit's philosophy, installation, or configuration.
5. Use the [Developer Guide] to explain how to create new tools, register them, or use helper functions.
6. If no tool seems relevant, say so and suggest looking at the official repository."""

        console.print(f"\n[bold blue]Asking AI ({profile_id}:{model}){age_str}...[/bold blue]")
        
        try:
            response = call_llm(provider, provider_config, system_prompt, query)
            if not response or not response.strip():
                console.print(Panel("[yellow]The AI returned an empty response. This can happen with some models or providers. Please try rephrasing your question or try again.[/yellow]", title="PAS AI Assistant", border_style="yellow"))
            else:
                console.print(Panel(response, title="PAS AI Assistant", border_style="green"))
        except Exception as e:
            error_msg = str(e)
            console.print(f"[bold red]Error:[/bold red] {error_msg}")
            if "403" in error_msg or "401" in error_msg:
                if questionary.confirm("The API token seems invalid. Would you like to re-configure it?").ask():
                    setup_llm(ai_models_config)
                    if not is_interactive:
                        # Re-run the call if not interactive
                        ai_models_config = load_pas_config("ai-models")
                        pas_config = load_pas_config("pas")
                        active_config_id = pas_config.get("active_ai_config_id")
                        active_config = ai_models_config.get("configs", {}).get(active_config_id, {})
                        profile_id = active_config.get("profile")
                        model = active_config.get("model")
                        profile_data = ai_models_config.get("profiles", {}).get(profile_id, {})
                        provider = profile_data.get("provider")
                        provider_token = profile_data.get("token")
                        provider_config = {"token": provider_token, "model": model}
                        try:
                            console.print(f"\n[bold blue]Retrying AI ({profile_id}:{model})...[/bold blue]")
                            response = call_llm(provider, provider_config, system_prompt, query)
                            if not response or not response.strip():
                                console.print(Panel("[yellow]The AI returned an empty response on retry. Please try again later or check your API status.[/yellow]", title="PAS AI Assistant", border_style="yellow"))
                            else:
                                console.print(Panel(response, title="PAS AI Assistant", border_style="green"))
                        except Exception as retry_e:
                            console.print(f"[bold red]Retry failed:[/bold red] {str(retry_e)}")

        if not is_interactive:
            break

def setup_llm(config):
    """Setup AI configuration using ai-ops.py or internal logic."""
    pas_config = load_pas_config("pas")
    active_config_id = pas_config.get("active_ai_config_id")

    # Menu for existing configs or new one
    choices = []
    for cfg_id in config.get("configs", {}).keys():
        is_active = cfg_id == active_config_id
        title = f"{cfg_id} {'(ACTIVE)' if is_active else ''}"
        choices.append({"title": title, "value": cfg_id})
    
    choices.append({"title": "[Manage AI Profiles/Configs (ai-ops)]", "value": "AI_OPS"})
    choices.append({"title": "[Add new configuration]", "value": "NEW"})
    choices.append({"title": "[Back]", "value": "BACK"})
    
    formatted_choices = format_menu_choices(choices, title_field="title", value_field="value")
    console.print("\n[bold cyan]AI Configuration Management:[/bold cyan]")
    selected = prompt_toolkit_menu(formatted_choices)
    
    if not selected or selected == "BACK":
        return

    if selected == "AI_OPS":
        # Call ai-ops.py
        subprocess.run([sys.executable, str(get_pas_root() / "services" / "ai-ops.py"), "list"])
        # After returning, reload config
        new_config = load_pas_config("ai-models")
        config.clear()
        config.update(new_config)
        return setup_llm(config)

    if selected == "NEW":
        # Select Profile
        profiles = config.get("profiles", {})
        if not profiles:
            console.print("[yellow]No profiles found. Let's create one.[/yellow]")
            provider = "openrouter"
            profile_id = questionary.text("Enter Profile ID (e.g., 'default'):").ask()
            if not profile_id: return
            token = questionary.password(f"Enter {provider} API Token:").ask()
            if not token: return
            config["profiles"][profile_id] = {"provider": provider, "token": token}
            save_pas_config("ai-models", config)
        else:
            profile_choices = [{"title": pid, "value": pid} for pid in profiles]
            profile_choices.append({"title": "[Create new profile]", "value": "NEW_PROFILE"})
            selected_profile = prompt_toolkit_menu(format_menu_choices(profile_choices, title_field="title", value_field="value"))
            if selected_profile == "NEW_PROFILE":
                provider = "openrouter"
                profile_id = questionary.text("Enter Profile ID:").ask()
                if not profile_id: return
                token = questionary.password(f"Enter {provider} API Token:").ask()
                if not token: return
                config["profiles"][profile_id] = {"provider": provider, "token": token}
                save_pas_config("ai-models", config)
            else:
                profile_id = selected_profile

        if not profile_id: return
        provider = config["profiles"][profile_id]["provider"]

        # Select Model
        model = "openai/gpt-4o-mini"
        if provider == "openrouter":
            console.print("[cyan]Fetching available models from OpenRouter...[/cyan]")
            source_models = get_openrouter_models()
            if not source_models:
                source_models = [{"id": "openai/gpt-4o-mini"}, {"id": "anthropic/claude-3.5-sonnet"}]
            
            menu = Menu("Select Model")
            for m_obj in source_models[:15]:
                m_id = m_obj.get("id")
                menu.add_option(m_id, m_id)
            
            menu.add_option("Other...", "__other__")
            model_choice = menu.run(loop=False)
            if model_choice == "__other__":
                model = questionary.text("Enter model ID:").ask()
            else:
                model = model_choice

        if not model: return

        # Save configuration
        config_id = f"{profile_id}:{model}"
        config["configs"][config_id] = {"profile": profile_id, "model": model}
        save_pas_config("ai-models", config)
        
        # Save app-specific choice
        pas_config["active_ai_config_id"] = config_id
        save_pas_config("pas", pas_config)
        console.print(f"[green]Configuration '{config_id}' saved and set as active for 'pas'.[/green]")
    else:
        # Switch to existing
        pas_config["active_ai_config_id"] = selected
        save_pas_config("pas", pas_config)
        console.print(f"[green]Switched to configuration '{selected}' for 'pas'.[/green]")


def get_openrouter_models():
    """Fetch and filter popular models from OpenRouter API."""
    url = "https://openrouter.ai/api/v1/models"
    try:
        req = urllib.request.Request(url, headers={
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit"
        })
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))
            if "data" in data:
                all_models = data["data"]
                def model_priority(m):
                    pid = m.get("id", "")
                    p_score = 0
                    for p in ["openai/", "anthropic/", "google/", "deepseek/", "meta-llama/"]:
                        if pid.startswith(p):
                            p_score = 10
                            break
                    if ":free" in pid or "-exp" in pid:
                        p_score -= 5
                    return p_score
                sorted_models = sorted(all_models, key=model_priority, reverse=True)
                return sorted_models
    except Exception as e:
        console.print(f"[yellow]Warning: Could not fetch models from OpenRouter: {e}[/yellow]")
    return []

def validate_llm_token(provider, token):
    """Check if the provided token is valid for the given provider. Returns (is_valid, metadata)."""
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/auth/key"
        headers = {
            "Authorization": f"Bearer {token or ''}",
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit",
            "User-Agent": "PAS-Toolkit/1.0"
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as res:
                if res.getcode() == 200:
                    resp_data = json.loads(res.read().decode("utf-8"))
                    return True, resp_data.get("data", {})
                return False, {}
        except Exception:
            return False, {}
    return True, {}

def call_llm(provider, provider_config, system_prompt, query):
    """Call the LLM API."""
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider_config['token']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit",
            "User-Agent": "PAS-Toolkit/1.0"
        }
        data = {
            "model": provider_config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ]
        }
        
        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req) as res:
                resp_data = json.loads(res.read().decode("utf-8"))
                if "choices" in resp_data:
                    return resp_data["choices"][0]["message"]["content"]
                else:
                    raise Exception(f"Unexpected response: {resp_data}")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_json = json.loads(error_body)
                if "error" in error_json:
                    msg = error_json["error"].get("message", error_body)
                    raise Exception(f"OpenRouter Error ({e.code}): {msg}")
            except:
                pass
            raise Exception(f"HTTP Error {e.code}: {e.reason}\n{error_body}")
    else:
        raise Exception(f"Unsupported provider: {provider}")

def _parse_tool_constants(content: str) -> tuple[str | None, str | None]:
    """Extract TOOL_ID and TOOL_SHORT_DESC from file content."""
    tool_id = None
    short_desc = None
    m = re.search(r'TOOL_ID\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        tool_id = m.group(1).strip()
    m = re.search(r'TOOL_SHORT_DESC\s*=\s*["\']((?:[^"\'\\]|\\.)*)["\']', content)
    if m:
        short_desc = m.group(1).strip().replace('\\n', '\n')
    return (tool_id, short_desc)

def get_tools_info() -> list[tuple[str, str]]:
    """Scans the repository and returns a list of (tool_name, description)."""
    root = get_pas_root()
    marker = "@pas-executable"
    tools = []

    for path, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'bin']
        for file in files:
            file_path = Path(path) / file
            if file_path.suffix not in ['.py', '', '.sh', '.bb'] or file_path.name.startswith("legacy-"):
                continue
            
            try:
                with open(file_path, 'r', errors='ignore') as f:
                    content = f.read(8192)
                    lines = content.splitlines()[:80]
                    tool_id, short_desc = _parse_tool_constants(content)
                    name = tool_id if tool_id else file_path.stem
                    desc = short_desc

                    if not desc:
                        found_marker_at = -1
                        for i, line in enumerate(lines):
                            clean = line.strip().strip(' "\'#*;')
                            if clean == marker:
                                found_marker_at = i
                                break
                        
                        if found_marker_at != -1:
                            desc_lines = []
                            start_index = found_marker_at + 1
                            while start_index < len(lines) and not lines[start_index].strip():
                                start_index += 1
                            
                            if start_index < len(lines):
                                first_line = lines[start_index].strip()
                                if first_line.startswith('"""') or first_line.startswith("'''"):
                                    q = '"""' if first_line.startswith('"""') else "'''"
                                    if first_line.count(q) >= 2:
                                        desc_lines.append(first_line.replace(q, "").strip())
                                    else:
                                        desc_lines.append(first_line.replace(q, "").strip())
                                        for line in lines[start_index + 1:]:
                                            clean = line.strip()
                                            if q in clean:
                                                content_part = clean.replace(q, "").strip()
                                                if content_part: desc_lines.append(content_part)
                                                break
                                            desc_lines.append(clean)
                                elif first_line.startswith('#'):
                                    for line in lines[start_index:]:
                                        clean = line.strip()
                                        if clean.startswith('#'):
                                            content_part = clean.lstrip('# ').strip()
                                            if content_part: desc_lines.append(content_part)
                                            elif desc_lines: break
                                        elif clean: break
                                        else: break
                                else:
                                    desc_lines.append(first_line)
                            desc = "\n  ".join([d for d in desc_lines if d]) if desc_lines else "No description available"
                    if not desc:
                        desc = "No description available"
                    tools.append((name, desc))
            except Exception:
                continue
    return sorted(tools)

def cmd_list(args):
    """List all available PAS tools and their descriptions."""
    tools = get_tools_info()
    num_tools = len(tools)
    padding = 1 if num_tools < 10 else (2 if num_tools < 100 else 3)
    print(f"\n{'No.':<{padding + 2}} {'Tool':<20} Description")
    print("-" * (padding + 2 + 20 + 40))
    for i, (name, desc) in enumerate(tools, 1):
        num_str = str(i).zfill(padding)
        print(f"{num_str:<{padding + 2}} {name:<20} {desc}")
    print(f"\nTotal: {num_tools} tools found.\n")

def main():
    check_platform()
    version = get_pas_version()
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-v", "--version", action="version", version=f"PAS Toolkit {version}")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    info_text = f"""
[bold]PAS Toolkit Manager (v{version})[/bold]

The central command-line utility for managing this toolkit:
- [cyan]list[/cyan]: Scans the repository and displays all available self-documenting tools.
- [cyan]ask[/cyan]: Query an AI assistant about which tool to use for your task.
- [cyan]upgrade[/cyan]: Automatically pulls latest changes and refreshes system symlinks.
- [cyan]up[/cyan]: Alias for [cyan]upgrade[/cyan].
- [cyan]repo[/cyan]: Opens the official GitHub repository in your browser.
"""
    if len(sys.argv) == 1:
        console.print(Panel(info_text.strip(), title="pas", border_style="blue"))
        console.print("\n")

    subparsers.add_parser("upgrade", help="Pull latest changes and run setup").set_defaults(func=cmd_upgrade)
    subparsers.add_parser("up", help="Alias for 'upgrade'").set_defaults(func=cmd_upgrade)
    subparsers.add_parser("list", help="List all available PAS tools").set_defaults(func=cmd_list)
    subparsers.add_parser("repo", help="Open official GitHub repository").set_defaults(func=cmd_repo)
    ask_parser = subparsers.add_parser("ask", help="Ask an AI assistant about PAS tools")
    ask_parser.add_argument("query", nargs="*", help="The question to ask")
    ask_parser.set_defaults(func=cmd_ask)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        if len(sys.argv) > 1:
            parser.print_help()
        sys.exit(0)
    args.func(args)

if __name__ == "__main__":
    main()
