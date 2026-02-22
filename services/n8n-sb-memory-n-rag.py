#!/usr/bin/env python3
"""
@pas-executable
Setup Supabase Vector DB as memory for n8n AI Agents.
"""

import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
import os
from pathlib import Path

# Add the directory containing this script to sys.path to allow imports from nearby files
sys.path.append(str(Path(__file__).resolve().parent))

import argparse
import json
import urllib.request
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

import questionary
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import (
    load_pas_config, 
    save_pas_config,
    detect_supabase_binary,
    copy_to_clipboard,
    prompt_toolkit_menu,
    format_menu_choices
)
from helpers.supabase import (
    get_active_token,
    get_active_org_info,
    get_projects,
    get_api_keys,
    get_project_pooling_config,
    supabase_api_request,
    run_sql_via_psql,
    fetch_sql_results,
    detect_pooler_prefix
)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

def main():
    parser = argparse.ArgumentParser(description="Setup Supabase Vector DB for n8n.")
    parser.parse_args()

    config = load_pas_config("supabase")
    token = get_active_token(config)

    if not token:
        console.print("[bold red]Error:[/bold red] No active Supabase organization. Run `sb-acs` to login first.")
        sys.exit(1)

    console.print("[bold blue]n8n-sb-memory-n-rag[/bold blue] - Setting up Supabase for n8n AI Agent\n")

    # Display general info/reminder
    info_text = """
This script helps you set up Supabase as a backend for n8n AI Agents:

1. [bold]Postgres Chat Memory[/bold]: Stores chat history so the agent remembers past interactions.
   [italic]Note: This script can initialize the table for you and enable RLS.[/italic]

2. [bold]Supabase Vector Store[/bold]: Enables Retrieval-Augmented Generation (RAG) by storing 
   and searching vectorized documents.
   [italic]Note: This requires initialization (which this script can automate).[/italic]

[bold green]What this script does:[/bold green]
- Fetches database connection details for the [cyan]Postgres Chat Memory[/cyan] node.
- Retrieves API credentials for the [cyan]Supabase Vector Store[/cyan] node.
- Provides/Executes SQL to initialize [cyan]pgvector[/cyan], [cyan]documents[/cyan] table, and [cyan]n8n_chat_histories[/cyan] table (with RLS).
"""
    console.print(Panel(info_text.strip(), title="n8n-sb-memory-n-rag", border_style="blue"))
    console.print("\n")

    # Loop for project selection (allows switching orgs)
    selected_project = None
    while not selected_project:
        active_org = get_active_org_info(config)
        if not active_org:
            console.print("[bold red]Error:[/bold red] No active Supabase organization. Run `sb-acs` to login first.")
            sys.exit(1)

        org_name = active_org.get('name', 'Unknown')
        org_email = active_org.get('email', 'N/A')
        console.print(f"Active Organization: [bold cyan]{org_name}[/bold cyan] <{org_email}>")

        # 1. Select Project
        projects = get_projects(token)
        # We want the full project object as the value
        choices = format_menu_choices(projects, title_field="name", value_field=None)
        
        choices.append(questionary.Choice(title="o. 🔄 Switch Supabase Organization", value="SWITCH_ORG"))
        choices.append(questionary.Choice(title="q. Quit", value="QUIT"))

        console.print("\n[bold]Select the Supabase project to use:[/bold] (Use arrows or press 1-9, o, q)")
        hotkeys = [str(i) for i in range(1, len(projects) + 1)] + ['o', 'q']
        choice = prompt_toolkit_menu(choices, hotkeys=hotkeys)
        if choice == "QUIT" or not choice:
            sys.exit(0)
        
        if choice == "SWITCH_ORG":
            # Replicate switch logic from sb-acs.py
            orgs = config.get("organizations", {})
            if not orgs:
                console.print("[yellow]No other organizations found. Use `sb-acs` to add more.[/yellow]")
                continue

            items = list(orgs.items())
            active_id = config.get("active_org_id")
            
            # Prepare org list for format_menu_choices
            org_list = []
            for org_id, data in items:
                label = f"{data.get('name')} <{data.get('email', 'N/A')}> ({org_id})"
                if org_id == active_id:
                    label = f"[*] {label}"
                org_list.append({"label": label, "id": org_id})

            switch_choices = format_menu_choices(org_list, title_field="label", value_field="id")
            switch_choices.append(questionary.Choice(title="q. Cancel", value="CANCEL"))

            console.print("\n[bold]Select Organization to Activate:[/bold] (Use arrows or press 1-9, q)")
            hotkeys = [str(i) for i in range(1, len(org_list) + 1)] + ['q']
            selected_id = prompt_toolkit_menu(switch_choices, hotkeys=hotkeys)
            if selected_id and selected_id != "CANCEL":
                config["active_org_id"] = selected_id
                save_pas_config("supabase", config)
                token = get_active_token(config) # Update token for the loop
                console.print(f"[green]Switched to organization: {orgs[selected_id].get('name')}[/green]\n")
            continue
        
        # If we reach here, a project was selected
        selected_project = choice

    project_ref = selected_project['id']
    region = selected_project.get('region', 'us-east-1')

    # 2. Get API Keys
    keys = get_api_keys(project_ref, token)
    service_role_key = keys.get("service_role")
    anon_key = keys.get("anon")
    
    if not service_role_key:
        console.print("[bold red]Error:[/bold red] Could not fetch service_role key.")
        sys.exit(1)

    # 3. Connection Details
    # Default values
    host = f"aws-0-{region}.pooler.supabase.com"
    user = f"postgres.{project_ref}"
    db_name = "postgres"
    port = "6543" 
    api_url = f"https://{project_ref}.supabase.co"

    # 3.5 Check for password in PAS config (needed for host detection fallback)
    passwords = config.get("project_passwords", {})
    db_password = passwords.get(project_ref)

    # Try to get exact pooler host from API
    console.print(f"Fetching database configuration for [bold cyan]{project_ref}[/bold cyan]...")
    pool_config = get_project_pooling_config(project_ref, token)
    
    api_host_found = False
    if pool_config and isinstance(pool_config, dict):
        # The API usually returns a full connection string or specific host fields
        # Note: Response format can vary slightly between API versions
        conn_string = pool_config.get("connection_string")
        if conn_string:
            try:
                # Basic parsing of postgresql://user:pass@host:port/db
                parts = conn_string.split("@")[-1].split("/")[0].split(":")
                host = parts[0]
                if len(parts) > 1:
                    port = parts[1]
                api_host_found = True
                console.print(f"Retrieved pooler host from API: [bold green]{host}[/bold green]")
            except:
                pass
    
    if not api_host_found and db_password:
        console.print(f"[yellow]Could not retrieve host from API. Falling back to detection...[/yellow]")
        prefix = detect_pooler_prefix(region, project_ref, db_password)
        host = f"{prefix}-{region}.pooler.supabase.com"
        console.print(f"Detected pooler: [bold cyan]{host}[/bold cyan]")
    elif not api_host_found:
        console.print(f"[yellow]Could not retrieve host from API and no password for detection. Using default: {host}[/yellow]")

    # 4. Prompt for embedding dimension
    console.print("\n[bold]Embedding Dimension Configuration:[/bold]")
    dimension_default = 768
    dimension_help = f"""
[bold]Default:[/bold] {dimension_default} (Google Vertex embeddings)
[bold]Reminder:[/bold] OpenAI embeddings dimension is 1536
"""
    console.print(Panel(dimension_help.strip(), border_style="yellow"))
    
    dimension_input = questionary.text(
        f"Enter embedding dimension (default: {dimension_default}):",
        default=str(dimension_default)
    ).ask()
    
    try:
        embedding_dimension = int(dimension_input) if dimension_input else dimension_default
    except ValueError:
        console.print(f"[yellow]Invalid input, using default: {dimension_default}[/yellow]")
        embedding_dimension = dimension_default
    
    console.print(f"[green]Using embedding dimension: {embedding_dimension}[/green]\n")

    # 5. Initialize Vector Store and Chat Memory SQL
    sql_init = f"""
-- 1. Enable pgvector extension
create extension if not exists vector;

-- 2. Create documents table for Vector Store
create table if not exists documents (
  id bigserial primary key,
  content text,
  metadata jsonb,
  embedding vector({embedding_dimension})
);

-- 3. Create match_documents function for RAG
create or replace function match_documents (
  query_embedding vector({embedding_dimension}),
  match_threshold float default 0.0,
  match_count int default 10,
  filter jsonb default '{{}}'
)
returns table (
  id bigint,
  content text,
  metadata jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    documents.id,
    documents.content,
    documents.metadata,
    1 - (documents.embedding <=> query_embedding) as similarity
  from documents
  where (1 - (documents.embedding <=> query_embedding) > match_threshold)
    and (documents.metadata @> filter)
  order by documents.embedding <=> query_embedding
  limit least(match_count, 100);
end;
$$;

-- 4. Create n8n_chat_histories table for Chat Memory
create table if not exists n8n_chat_histories (
  id serial primary key,
  session_id text not null,
  message jsonb not null,
  created_at timestamptz default now()
);

-- 5. Enable RLS on n8n_chat_histories
alter table n8n_chat_histories enable row level security;
"""

    console.print("\n[bold green]Preparation Steps:[/bold green]")
    
    # 3.6 Ensure we have the DB password (needed for n8n and/or auto-init)
    if not db_password:
        console.print("[yellow]Database password not found in PAS configuration.[/yellow]")
        console.print(f"You can set or reset your password here: [bold cyan]https://supabase.com/dashboard/project/{project_ref}/settings/database[/bold cyan]")
        console.print("[italic]Note: If the web dashboard says 'Project not found', please check if you are logged into the correct Supabase account with proper access rights.[/italic]\n")
        if questionary.confirm("Would you like to enter it now to save it for n8n and auto-initialization?").ask():
            db_password = questionary.password("Enter Supabase DB Password:").ask()
            if db_password:
                # Save to ~/.pas/supabase.json
                if "project_passwords" not in config:
                    config["project_passwords"] = {}
                config["project_passwords"][project_ref] = db_password
                save_pas_config("supabase", config)
                console.print("[green]Password saved to PAS configuration.[/green]")

    # --- Schema Validation ---
    schema_ok = False
    documents_table_exists = False
    chat_table_exists = False
    chat_rls_enabled = False
    mismatch_details = []
    
    if db_password:
        console.print("Checking existing database schema...")
        
        # 1. Check documents table
        query_docs = "SELECT 1 FROM information_schema.tables WHERE table_name = 'documents';"
        if fetch_sql_results(host, port, user, db_name, db_password, query_docs):
            documents_table_exists = True
            # Check columns
            query_cols = """
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name = 'documents';
            """
            cols = fetch_sql_results(host, port, user, db_name, db_password, query_cols)
            col_map = {c['column_name']: c for c in cols}
            
            required = {'content': 'text', 'metadata': 'jsonb', 'embedding': 'USER-DEFINED'}
            for col, expected_type in required.items():
                if col not in col_map:
                    mismatch_details.append(f"Documents: Missing column [bold red]{col}[/bold red]")
                elif col == 'embedding':
                    query_dim = "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'documents'::regclass AND attname = 'embedding';"
                    dim_res = fetch_sql_results(host, port, user, db_name, db_password, query_dim)
                    if dim_res:
                        dim = int(dim_res[0]['atttypmod'])
                        if dim != embedding_dimension:
                            mismatch_details.append(f"Documents: Column 'embedding' dimension is [yellow]{dim}[/yellow] (expected {embedding_dimension})")
            
            if not mismatch_details:
                console.print("[green]✅ 'documents' table exists with correct schema.[/green]")
            else:
                for detail in mismatch_details: console.print(f"  ⚠️ {detail}")
        else:
            console.print("[yellow]ℹ️ 'documents' table does not exist yet.[/yellow]")

        # 2. Check n8n_chat_histories table
        query_chat = "SELECT 1 FROM information_schema.tables WHERE table_name = 'n8n_chat_histories';"
        if fetch_sql_results(host, port, user, db_name, db_password, query_chat):
            chat_table_exists = True
            
            # Check columns
            query_chat_cols = "SELECT column_name FROM information_schema.columns WHERE table_name = 'n8n_chat_histories';"
            chat_cols = [c['column_name'] for c in fetch_sql_results(host, port, user, db_name, db_password, query_chat_cols)]
            
            chat_required = ['session_id', 'message', 'created_at']
            chat_missing = [c for c in chat_required if c not in chat_cols]
            
            if not chat_missing:
                console.print("[green]✅ 'n8n_chat_histories' table exists with correct columns.[/green]")
            else:
                console.print(f"[yellow]⚠️ 'n8n_chat_histories' exists but missing columns: {', '.join(chat_missing)}[/yellow]")
                chat_table_exists = False

            # Check RLS
            query_rls = "SELECT relrowsecurity FROM pg_class WHERE relname = 'n8n_chat_histories';"
            rls_res = fetch_sql_results(host, port, user, db_name, db_password, query_rls)
            if rls_res and rls_res[0].get('relrowsecurity'):
                chat_rls_enabled = True
                console.print("[green]✅ RLS is enabled on 'n8n_chat_histories'.[/green]")
            else:
                console.print("[yellow]⚠️ RLS is NOT enabled on 'n8n_chat_histories'.[/yellow]")
        else:
            console.print("[yellow]ℹ️ 'n8n_chat_histories' table does not exist yet.[/yellow]")

        # 3. Check match_documents function
        query_func = "SELECT proargnames FROM pg_proc WHERE proname = 'match_documents';"
        func_res = fetch_sql_results(host, port, user, db_name, db_password, query_func)
        func_exists = len(func_res) > 0
        if func_exists:
            proargnames = func_res[0].get('proargnames', '')
            if 'filter' in proargnames:
                console.print("[green]✅ 'match_documents' function exists with 'filter' support.[/green]")
            else:
                console.print("[yellow]⚠️ 'match_documents' function exists but missing 'filter' parameter.[/yellow]")
                func_exists = False
        else:
            console.print("[yellow]ℹ️ 'match_documents' function does not exist yet.[/yellow]")

        schema_ok = documents_table_exists and chat_table_exists and chat_rls_enabled and func_exists and not mismatch_details

    psql_available = shutil.which("psql") is not None
    auto_success = False

    if psql_available and db_password:
        if not schema_ok:
            if questionary.confirm("Schema is incomplete or missing. Would you like to (re)initialize the Vector Store and Chat Memory now?").ask():
                console.print(f"Running SQL on {host}...")
                res = run_sql_via_psql(host, port, user, db_name, db_password, sql_init)
                if res.returncode == 0:
                    console.print("[bold green]✅ Database initialized successfully![/bold green]")
                    auto_success = True
                else:
                    console.print(f"[bold red]❌ SQL execution failed:[/bold red]\n{res.stderr}")
                    console.print("[yellow]Falling back to manual setup instructions.[/yellow]")
        else:
            console.print("[blue]Database is already properly initialized. Skipping auto-init.[/blue]")
            auto_success = True
    elif not psql_available:
        console.print("[yellow]'psql' CLI not found. Skipping auto-initialization.[/yellow]")
    elif not db_password:
        console.print("[yellow]Skipping auto-initialization (no password).[/yellow]")

    if not auto_success:
        console.print("\n1. [bold]Manual Initialization:[/bold] Run the following SQL in the Supabase SQL Editor:")
        panel_sql = Panel(sql_init.strip(), title="SQL to execute in Supabase Editor", border_style="cyan")
        console.print(panel_sql)
        
        questionary.confirm("Confirm once you have executed the SQL in the Supabase Dashboard:").ask()

    # 5. Display Summary for n8n
    db_password_display = db_password if db_password else "[Your Supabase DB Password]"
    
    table = Table(title="n8n Configuration Summary", show_header=True, header_style="bold magenta")
    table.add_column("Node Type", style="cyan", no_wrap=True)
    table.add_column("Field", style="green", no_wrap=True)
    table.add_column("Value", style="white", overflow="fold") # Fold ensures long keys don't truncate

    # Postgres Chat Memory
    table.add_row("Postgres Chat Memory", "Host", host)
    table.add_row("Postgres Chat Memory", "Database", db_name)
    table.add_row("Postgres Chat Memory", "User", user)
    table.add_row("Postgres Chat Memory", "Port", port)
    table.add_row("Postgres Chat Memory", "Password", db_password_display)
    table.add_row("Postgres Chat Memory", "Table Name", "n8n_chat_histories")
    
    chat_init_status = "✅ Initialized & RLS Enabled" if (chat_table_exists and chat_rls_enabled) or auto_success else "⚠️ Pending"
    table.add_row("Postgres Chat Memory", "[italic]Initialization[/italic]", f"[italic]{chat_init_status}[/italic]")

    table.add_section()

    # Supabase Vector Store
    table.add_row("Supabase Vector Store", "Host URL", api_url)
    table.add_row("Supabase Vector Store", "Secret Key (legacy: service_role)", service_role_key)
    if anon_key:
        table.add_row("Supabase Vector Store", "Publishable Key (legacy: anon)", anon_key)
    table.add_row("Supabase Vector Store", "Table Name", "documents")
    table.add_row("Supabase Vector Store", "Query Name", "match_documents")
    
    vec_init_status = "✅ Initialized (via this script)" if documents_table_exists or auto_success else "⚠️ Pending Manual SQL Execution"
    table.add_row("Supabase Vector Store", "[italic]Initialization[/italic]", f"[italic]{vec_init_status}[/italic]")

    console.print("\n")
    console.print(table)
    
    console.print("\n[bold yellow]Note:[/bold yellow] If the connection fails, ensure your project host and keys are correct.")
    console.print("Manage Database & Passwords:")
    console.print(f"  [bold cyan]https://supabase.com/dashboard/project/{project_ref}/settings/database[/bold cyan]")
    console.print("Manage API Keys (Secret & Publishable):")
    console.print(f"  [bold cyan]https://supabase.com/dashboard/project/{project_ref}/settings/api-keys[/bold cyan]")
    console.print("\n[italic]If any link shows 'Project not found', verify your browser session has the correct account access.[/italic]")

    # 5.5 Security Reminder for Chat Memory
    if not chat_rls_enabled and not auto_success:
        rls_reminder = """
[bold red]Security Action Required:[/bold red]
The [cyan]n8n_chat_histories[/cyan] table is missing Row Level Security (RLS).
To protect your chat data, you [bold]MUST[/bold] enable it:

[bold cyan]ALTER TABLE n8n_chat_histories ENABLE ROW LEVEL SECURITY;[/bold cyan]

Run this in your Supabase SQL Editor once the table exists.
"""
        console.print(Panel(rls_reminder.strip(), title="Postgres Chat Memory Security", border_style="red"))
    else:
        console.print("\n[bold green]✅ Postgres Chat Memory Security:[/bold green] RLS is already enabled on [cyan]n8n_chat_histories[/cyan].")

    # 6. Optional: Copy to Clipboard
    if shutil.which("pbcopy") or shutil.which("xclip") or shutil.which("xsel"):
        while True:
            copy_choices = [
                questionary.Choice("1. Copy Secret Key", value="SECRET_KEY"),
                questionary.Choice("2. Copy Publishable Key", value="PUB_KEY"),
                questionary.Choice("q. Done / Skip", value="q")
            ]
            
            console.print("\n[bold]Clipboard Options:[/bold] (Use arrows or press 1, 2, q)")
            choice = prompt_toolkit_menu(copy_choices)
            
            if not choice or choice == 'q':
                break
                
            val = None
            label = ""
            if choice == "SECRET_KEY":
                val, label = service_role_key, "Secret Key"
            elif choice == "PUB_KEY":
                val, label = anon_key, "Publishable Key"
                
            if val:
                if copy_to_clipboard(val):
                    console.print(f"[green]✅ {label} copied to clipboard![/green]")
                else:
                    console.print(f"[red]❌ Failed to copy {label} to clipboard.[/red]")
            else:
                console.print(f"[yellow]⚠️ {label} not available.[/yellow]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
