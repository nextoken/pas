#!/usr/bin/env python3
import json
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any

def cf_api_request(endpoint: str, token: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Make a request to the Cloudflare API."""
    url = f"https://api.cloudflare.com/client/v4/{endpoint}"
    
    encoded_data = None
    if data:
        encoded_data = json.dumps(data).encode("utf-8")
        
    req = urllib.request.Request(url, data=encoded_data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP Error {e.code}"}]}
    except Exception as e:
        return {"success": False, "errors": [{"message": str(e)}]}

def get_zones(token: str) -> List[Dict[str, Any]]:
    """Fetch the list of zones (domains)."""
    data = cf_api_request("zones", token)
    if data and data.get("success"):
        return data.get("result", [])
    return []

def get_dns_records(token: str, zone_id: str) -> List[Dict[str, Any]]:
    """Fetch DNS records for a specific zone."""
    data = cf_api_request(f"zones/{zone_id}/dns_records", token)
    if data and data.get("success"):
        return data.get("result", [])
    return []

def create_dns_record(token: str, zone_id: str, rec_type: str, name: str, content: str, proxied: bool = True) -> Dict[str, Any]:
    """Create a new DNS record."""
    data = {
        "type": rec_type,
        "name": name,
        "content": content,
        "ttl": 1,  # Auto
        "proxied": proxied
    }
    return cf_api_request(f"zones/{zone_id}/dns_records", token, method="POST", data=data)

def update_dns_record(token: str, zone_id: str, record_id: str, rec_type: str, name: str, content: str, proxied: bool = True) -> Dict[str, Any]:
    """Update an existing DNS record."""
    data = {
        "type": rec_type,
        "name": name,
        "content": content,
        "ttl": 1,  # Auto
        "proxied": proxied
    }
    return cf_api_request(f"zones/{zone_id}/dns_records/{record_id}", token, method="PUT", data=data)

def delete_dns_record(token: str, zone_id: str, record_id: str) -> Dict[str, Any]:
    """Delete a DNS record."""
    return cf_api_request(f"zones/{zone_id}/dns_records/{record_id}", token, method="DELETE")

def create_tunnel(token: str, account_id: str, name: str) -> Dict[str, Any]:
    """Create a Cloudflare tunnel."""
    url = f"accounts/{account_id}/cfd_tunnel"
    data = {
        "name": name,
        "config_src": "cloudflare"
    }
    return cf_api_request(url, token, method="POST", data=data)

def get_tunnel(token: str, account_id: str, tunnel_id: str) -> Dict[str, Any]:
    """Fetch details for a specific tunnel."""
    url = f"accounts/{account_id}/cfd_tunnel/{tunnel_id}"
    return cf_api_request(url, token)

def list_tunnels(token: str, account_id: str) -> List[Dict[str, Any]]:
    """List Cloudflare tunnels for an account."""
    url = f"accounts/{account_id}/cfd_tunnel"
    data = cf_api_request(url, token)
    if data and data.get("success"):
        return data.get("result", [])
    return []

def get_tunnel_token(token: str, account_id: str, tunnel_id: str) -> Optional[str]:
    """Fetch the connector token for an existing tunnel."""
    url = f"accounts/{account_id}/cfd_tunnel/{tunnel_id}/token"
    data = cf_api_request(url, token)
    if data and data.get("success"):
        return data.get("result")
    return None

def delete_tunnel(token: str, account_id: str, tunnel_id: str) -> Dict[str, Any]:
    """Delete a Cloudflare tunnel."""
    url = f"accounts/{account_id}/cfd_tunnel/{tunnel_id}"
    return cf_api_request(url, token, method="DELETE")

def update_tunnel(token: str, account_id: str, tunnel_id: str, name: str) -> Dict[str, Any]:
    """Update a Cloudflare tunnel's metadata (e.g. name)."""
    url = f"accounts/{account_id}/cfd_tunnel/{tunnel_id}"
    data = {"name": name}
    return cf_api_request(url, token, method="PATCH", data=data)

def update_tunnel_configuration(token: str, account_id: str, tunnel_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Update Cloudflare tunnel configuration (ingress rules, etc.)."""
    url = f"accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    return cf_api_request(url, token, method="PUT", data=config)

def get_tunnel_configuration(token: str, account_id: str, tunnel_id: str) -> Dict[str, Any]:
    """Fetch Cloudflare tunnel configuration (ingress rules, etc.)."""
    url = f"accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    return cf_api_request(url, token)

def get_user_details(token: str) -> Dict[str, Any]:
    """Fetch Cloudflare user details (including email)."""
    return cf_api_request("user", token)

def get_token_info(token: str, account_id: Optional[str] = None, debug: bool = False) -> Optional[Dict[str, Any]]:
    """
    Get information about the current API token, including its name/label and type.
    Returns a dict with token info and a 'token_type' field ('user' or 'account').
    Returns None if the token doesn't have permission to read token info.
    
    Required Permission: The token must have "API Tokens Read" permission
    (or "API Tokens Write") to retrieve its own name/details.
    
    Supports both user-owned and account-owned tokens:
    - User-owned: uses /user/tokens/ endpoints
    - Account-owned: uses /accounts/{account_id}/tokens/ endpoints
    
    Uses a two-step process:
    1. Verify the token to get its ID (works for any valid token)
    2. Fetch detailed token info using the ID to get the name (requires API Tokens Read)
    """
    """
    Get information about the current API token, including its name/label.
    Returns None if the token doesn't have permission to read token info.
    
    Required Permission: The token must have "API Tokens Read" permission
    (or "API Tokens Write") to retrieve its own name/details.
    
    Supports both user-owned and account-owned tokens:
    - User-owned: uses /user/tokens/ endpoints
    - Account-owned: uses /accounts/{account_id}/tokens/ endpoints
    
    Uses a two-step process:
    1. Verify the token to get its ID (works for any valid token)
    2. Fetch detailed token info using the ID to get the name (requires API Tokens Read)
    """
    # Step 1: Try both user-owned and account-owned token verify
    # Some tokens might work with both, so we try both and use whichever succeeds
    user_verify_res = cf_api_request("user/tokens/verify", token)
    account_verify_res = None
    
    if account_id:
        account_verify_res = cf_api_request(f"accounts/{account_id}/tokens/verify", token)
    
    if debug:
        print(f"DEBUG: User token verify: {user_verify_res.get('success') if user_verify_res else 'None'}")
        if account_id:
            print(f"DEBUG: Account token verify: {account_verify_res.get('success') if account_verify_res else 'None'}")
    
    # Prefer account-owned if both work, otherwise use whichever works
    verify_res = None
    is_account_token = False
    
    if account_verify_res and account_verify_res.get("success"):
        verify_res = account_verify_res
        is_account_token = True
    elif user_verify_res and user_verify_res.get("success"):
        verify_res = user_verify_res
        is_account_token = False
    
    if not verify_res or not verify_res.get("success"):
        if debug:
            print("DEBUG: Token verify failed for both user and account endpoints")
            if user_verify_res:
                print(f"DEBUG: User verify errors: {user_verify_res.get('errors')}")
            if account_verify_res:
                print(f"DEBUG: Account verify errors: {account_verify_res.get('errors')}")
        return None
    
    token_id = verify_res.get("result", {}).get("id")
    if not token_id:
        if debug:
            print("DEBUG: Token verify succeeded but no ID in result")
            print(f"DEBUG: Verify result keys: {list(verify_res.get('result', {}).keys())}")
        return verify_res.get("result", {})  # Return basic info if no ID
    
    if debug:
        print(f"DEBUG: Token ID: {token_id}, Using {'account' if is_account_token else 'user'} endpoint")
    
    # Step 2: Fetch detailed token info using the ID to get the name
    # Use the same endpoint type that worked for verify
    if is_account_token and account_id:
        token_detail_res = cf_api_request(f"accounts/{account_id}/tokens/{token_id}", token)
    else:
        token_detail_res = cf_api_request(f"user/tokens/{token_id}", token)
    
    if debug:
        print(f"DEBUG: Token detail fetch success: {token_detail_res.get('success') if token_detail_res else 'None'}")
        if token_detail_res and not token_detail_res.get("success"):
            print(f"DEBUG: Token detail errors: {token_detail_res.get('errors')}")
        elif token_detail_res and token_detail_res.get("success"):
            result = token_detail_res.get("result", {})
            print(f"DEBUG: Token detail result keys: {list(result.keys())}")
            print(f"DEBUG: Token name in result: {result.get('name')}")
    
    if token_detail_res and token_detail_res.get("success"):
        result = token_detail_res.get("result", {})
        # Add token type to the result
        result["token_type"] = "account" if is_account_token else "user"
        return result
    
    # Fallback: return basic info from verify if detail fetch fails
    if debug:
        print("DEBUG: Token detail fetch failed, returning basic verify info")
    fallback_result = verify_res.get("result", {})
    # Add token type even to fallback result
    if fallback_result:
        fallback_result["token_type"] = "account" if is_account_token else "user"
    return fallback_result

def create_access_app(token: str, account_id: str, name: str, domain: str) -> Dict[str, Any]:
    """Create a Cloudflare Access Application."""
    url = f"accounts/{account_id}/access/apps"
    data = {
        "name": name,
        "domain": domain,
        "type": "self_hosted",
        "session_duration": "24h"
    }
    return cf_api_request(url, token, method="POST", data=data)

def list_access_apps(token: str, account_id: str) -> List[Dict[str, Any]]:
    """List Cloudflare Access Applications."""
    url = f"accounts/{account_id}/access/apps"
    data = cf_api_request(url, token)
    if data and data.get("success"):
        return data.get("result", [])
    return []

def update_access_app(token: str, account_id: str, app_id: str, name: str, domain: str) -> Dict[str, Any]:
    """Update an existing Cloudflare Access Application."""
    url = f"accounts/{account_id}/access/apps/{app_id}"
    data = {
        "name": name,
        "domain": domain,
        "type": "self_hosted",
        "session_duration": "24h"
    }
    return cf_api_request(url, token, method="PUT", data=data)

def create_access_policy(token: str, account_id: str, app_id: str, name: str, email: str) -> Dict[str, Any]:
    """Create a Cloudflare Access Policy for an application."""
    url = f"accounts/{account_id}/access/apps/{app_id}/policies"
    data = {
        "name": name,
        "decision": "allow",
        "include": [
            {"email": {"email": email}}
        ]
    }
    return cf_api_request(url, token, method="POST", data=data)

def delete_access_app(token: str, account_id: str, app_id: str) -> Dict[str, Any]:
    """Delete a Cloudflare Access Application."""
    url = f"accounts/{account_id}/access/apps/{app_id}"
    return cf_api_request(url, token, method="DELETE")

def verify_token_permissions(token: str, account_id: str) -> Dict[str, bool]:
    """
    Test the API token against required endpoints to verify permissions.
    Returns a dict of permission area -> success (bool).
    """
    results = {
        "Zones (Read)": False,
        "DNS (Edit)": False,
        "Tunnels (Edit)": False,
        "Access (Edit)": False,
        "API Tokens (Read)": False  # Optional: for displaying token name
    }

    # 1. Test Zones (Read)
    zones_res = cf_api_request("zones", token)
    if zones_res and zones_res.get("success"):
        results["Zones (Read)"] = True
        
        # 2. Test DNS (Edit) - Try to list DNS records for the first zone if available
        zones = zones_res.get("result", [])
        if zones:
            zone_id = zones[0]["id"]
            dns_res = cf_api_request(f"zones/{zone_id}/dns_records", token)
            if dns_res and dns_res.get("success"):
                results["DNS (Edit)"] = True
    
    # 3. Test Tunnels (Edit)
    tunnels_res = cf_api_request(f"accounts/{account_id}/cfd_tunnel", token)
    if tunnels_res and tunnels_res.get("success"):
        results["Tunnels (Edit)"] = True

    # 4. Test Access (Edit)
    access_res = cf_api_request(f"accounts/{account_id}/access/apps", token)
    if access_res and access_res.get("success"):
        results["Access (Edit)"] = True

    # 5. Test API Tokens (Read) - Optional, for displaying token name
    # First verify to get token ID, then try to fetch token details
    # Try user-owned tokens first, then account-owned if that fails
    verify_res = cf_api_request("user/tokens/verify", token)
    if not verify_res or not verify_res.get("success"):
        # Try account-owned token verify
        verify_res = cf_api_request(f"accounts/{account_id}/tokens/verify", token)
    
    if verify_res and verify_res.get("success"):
        token_id = verify_res.get("result", {}).get("id")
        if token_id:
            # Try user-owned endpoint first
            token_detail_res = cf_api_request(f"user/tokens/{token_id}", token)
            # If that fails, try account-owned endpoint
            if not token_detail_res or not token_detail_res.get("success"):
                token_detail_res = cf_api_request(f"accounts/{account_id}/tokens/{token_id}", token)
            if token_detail_res and token_detail_res.get("success"):
                results["API Tokens (Read)"] = True

    return results
