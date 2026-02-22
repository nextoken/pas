import os
import json
import urllib.request
import subprocess
import socket
import shutil
import ssl
from pathlib import Path
from typing import Optional, List, Dict, Any

def supabase_api_request(endpoint: str, token: str, method: str = "GET", data: Optional[Dict] = None) -> Optional[Any]:
    """Make a request to the Supabase API."""
    url = f"https://api.supabase.com/v1/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    
    body = None
    if data:
        body = json.dumps(data).encode()

    try:
        with urllib.request.urlopen(req, data=body, timeout=10) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        # print(f"DEBUG: HTTP Error {e.code} for {url}")
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        # print(f"DEBUG: Error for {url}: {e}")
        return {"error": str(e)}

def get_user_email(token: str) -> Optional[str]:
    """Fetch the user's email associated with the token."""
    orgs = supabase_api_request("organizations", token)
    if not orgs or not isinstance(orgs, list) or len(orgs) == 0:
        return None
    
    org_id = orgs[0].get("id")
    if not org_id:
        return None
        
    members = supabase_api_request(f"organizations/{org_id}/members", token)
    if members and isinstance(members, list) and len(members) > 0:
        return members[0].get("email")
            
    return None

def get_active_token(config: Dict[str, Any]) -> Optional[str]:
    """Get the token for the currently active organization from PAS config."""
    active_id = config.get("active_org_id")
    if not active_id:
        return None
    orgs = config.get("organizations", {})
    return orgs.get(active_id, {}).get("access_token")

def get_active_org_info(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Get detailed info about the currently active organization."""
    active_id = config.get("active_org_id")
    if not active_id:
        return None
    orgs = config.get("organizations", {})
    return orgs.get(active_id)

def check_local_link() -> Optional[str]:
    """Check if the current directory is linked to a Supabase project."""
    # Check common locations for project-ref
    paths = [
        Path(".supabase/project-ref"),
        Path("supabase/.temp/project-ref")
    ]
    for p in paths:
        if p.exists():
            return p.read_text().strip()
    return None

def get_org_for_project(project_ref: str, config: Dict[str, Any]) -> Optional[str]:
    """Identify which organization a project ref belongs to."""
    orgs = config.get("organizations", {})
    for org_id, data in orgs.items():
        token = data.get("access_token")
        if not token:
            continue
        
        projects = supabase_api_request("projects", token)
        if projects and isinstance(projects, list):
            for p in projects:
                if p.get("id") == project_ref:
                    return org_id
    return None

def get_projects(token: str) -> List[Dict[str, Any]]:
    """Fetch projects for a given token."""
    projects = supabase_api_request("projects", token)
    return projects if isinstance(projects, list) else []

def get_api_keys(project_ref: str, token: str) -> Dict[str, str]:
    """Fetch API keys for the project."""
    keys_data = supabase_api_request(f"projects/{project_ref}/api-keys", token)
    if keys_data and isinstance(keys_data, list):
        return {k.get("name"): k.get("api_key") for k in keys_data if k.get("name")}
    return {}

def get_project_pooling_config(project_ref: str, token: str) -> Optional[Dict[str, Any]]:
    """Fetch database pooling configuration including hostnames."""
    return supabase_api_request(f"projects/{project_ref}/config/database/pooling", token)

def test_connection(host: str, port: str, user: str, database: str, password: Optional[str], silent: bool = False) -> bool:
    """Test the database connection."""
    if not silent:
        print(f"Testing: {host}:{port} ({user})")
    
    try:
        # Check TCP first
        with socket.create_connection((host, int(port)), timeout=5):
            if not silent: print("  ✅ TCP Port reachable.")
    except Exception:
        if not silent: print("  ❌ TCP Port NOT reachable.")
        return False

    if password:
        psql_bin = shutil.which("psql")
        if psql_bin:
            from urllib.parse import quote_plus
            safe_password = quote_plus(password)
            uri = f"postgresql://{user}:{safe_password}@{host}:{port}/{database}"
            try:
                result = subprocess.run(
                    [psql_bin, "-X", uri, "-c", "SELECT 1;"],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                if result.returncode == 0:
                    if not silent: print("  ✅ Authentication successful.")
                    return True
                else:
                    if not silent: print("  ❌ Authentication failed.")
                    return False
            except Exception:
                if not silent: print("  ❌ Authentication timed out.")
                return False
        else:
            return True # TCP passed, psql missing
    return True

def detect_pooler_prefix(reg: str, project_ref: str, password: str) -> str:
    """Detect if the project is on aws-0 or aws-1."""
    user = f"postgres.{project_ref}"
    for prefix in ["aws-1", "aws-0"]:
        host = f"{prefix}-{reg}.pooler.supabase.com"
        # We test on session port 5432 which is more reliable for simple auth checks
        if test_connection(host, "5432", user, "postgres", password, silent=True):
            return prefix
    return "aws-0" # Default fallback

def run_sql_via_psql(host: str, port: str, user: str, database: str, password: str, sql: str) -> subprocess.CompletedProcess:
    """Execute SQL via psql command line."""
    from urllib.parse import quote_plus
    safe_password = quote_plus(password)
    uri = f"postgresql://{user}:{safe_password}@{host}:{port}/{database}"
    
    try:
        return subprocess.run(
            ["psql", "-X", uri],
            input=sql,
            capture_output=True,
            text=True,
            timeout=30
        )
    except Exception as e:
        return subprocess.CompletedProcess(args=["psql"], returncode=1, stdout="", stderr=str(e))

def fetch_sql_results(host: str, port: str, user: str, database: str, password: str, sql: str) -> List[Dict[str, Any]]:
    """Execute SQL via psql and return results as a list of dictionaries."""
    from urllib.parse import quote_plus
    safe_password = quote_plus(password)
    uri = f"postgresql://{user}:{safe_password}@{host}:{port}/{database}"
    
    # Use -t for tuples only, -A for unaligned, -F for separator, --json for json output (if supported)
    # Since psql version varies, --json might not be there. Let's use CSV-like with a safe separator.
    try:
        cmd = ["psql", "-X", uri, "-A", "-F", "|", "-c", sql]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        lines = result.stdout.strip().splitlines()
        if not lines:
            return []
        
        headers = lines[0].split("|")
        data = []
        for line in lines[1:]:
            # psql -A -F "|" prints "(n rows)" at the end if not using -t
            if line.startswith("(") and "rows)" in line:
                continue
            values = line.split("|")
            if len(values) == len(headers):
                data.append(dict(zip(headers, values)))
        return data
    except Exception:
        return []

def test_api(project_ref: str, anon_key: str) -> bool:
    """Test the Supabase REST API."""
    url = f"https://{project_ref}.supabase.co/rest/v1/"
    req = urllib.request.Request(url)
    req.add_header("apikey", anon_key)
    req.add_header("Authorization", f"Bearer {anon_key}")
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            try:
                unverified_context = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=10, context=unverified_context) as response:
                    return response.status == 200
            except: pass
    except: pass
    return False

def detect_native_login(supabase_bin: Path) -> Optional[Dict[str, Any]]:
    """Detect if there's a global login session via the CLI."""
    env = {k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"}
    cwd = Path.home() if os.path.isdir(".env") else None

    res_projects = subprocess.run(
        [str(supabase_bin), "projects", "list", "--output", "json"],
        capture_output=True, text=True, check=False, env=env, cwd=cwd
    )
    
    if res_projects.returncode == 0:
        try:
            projects = json.loads(res_projects.stdout)
            if isinstance(projects, list):
                org_names = {}
                res_orgs = subprocess.run(
                    [str(supabase_bin), "orgs", "list", "--output", "json"],
                    capture_output=True, text=True, check=False, env=env, cwd=cwd
                )
                if res_orgs.returncode == 0:
                    try:
                        orgs_data = json.loads(res_orgs.stdout)
                        if isinstance(orgs_data, list):
                            for o in orgs_data:
                                org_names[o.get("id")] = o.get("name")
                    except: pass

                org_ids = list(set(p.get("organization_id") for p in projects if p.get("organization_id")))
                org_info = [{"id": oid, "name": org_names.get(oid, "Unknown")} for oid in org_ids]

                return {
                    "logged_in": True,
                    "orgs": org_info,
                    "project_count": len(projects)
                }
        except: pass
    return None

def get_native_token() -> Optional[str]:
    """Attempt to find the native Supabase access token."""
    token_file = Path.home() / ".supabase" / "access-token"
    if token_file.exists():
        try:
            return token_file.read_text().strip()
        except: pass
            
    if os.uname().sysname == "Darwin":
        try:
            res = subprocess.run(
                ["security", "find-generic-password", "-s", "supabase", "-w"],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0:
                token = res.stdout.strip()
                if token: return token
        except: pass
    return None

def get_supabase_env(token: str) -> Dict[str, str]:
    """Return environment variables for Supabase CLI calls."""
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    return env

