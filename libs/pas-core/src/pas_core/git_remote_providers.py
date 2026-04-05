"""Host-aware Git remote resolution: GitHub via ``gh`` (fallback: URL parse), others via ``git`` + generic URL normalize."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from .git_utils import (
    github_remote_identity_key,
    normalize_github_remote_url,
    read_git_remote_raw_url,
)

RemoteKind = Literal["github", "generic"]


def read_remote_url_via_git_cli(
    project_root: Path, remote_name: str = "origin"
) -> Optional[str]:
    """Primary remote URL from ``git remote get-url`` (cwd = checkout)."""
    try:
        root = project_root.expanduser().resolve()
    except (OSError, ValueError):
        return None
    if not root.is_dir():
        return None
    name = (remote_name or "origin").strip() or "origin"
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", name],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    line = (res.stdout or "").strip()
    return line or None


def resolve_github_canonical_url_via_gh(project_root: Path) -> Optional[str]:
    """Canonical repo web URL from ``gh repo view`` when ``gh`` is installed and the repo is known to GitHub."""
    if not shutil.which("gh"):
        return None
    try:
        root = project_root.expanduser().resolve()
    except (OSError, ValueError):
        return None
    if not root.is_dir():
        return None
    try:
        res = subprocess.run(
            ["gh", "repo", "view", "--json", "url"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0 or not (res.stdout or "").strip():
        return None
    try:
        data = json.loads(res.stdout)
        u = data.get("url")
        if isinstance(u, str) and u.startswith("http"):
            return u.split("?", 1)[0].rstrip("/")
    except json.JSONDecodeError:
        pass
    return None


def connection_url_to_browse_url(conn: str) -> Optional[str]:
    """HTTPS-style URL for opening the repo in a browser (SSH/SCP input is mapped when possible)."""
    c = (conn or "").strip().strip('"').strip("'")
    if not c:
        return None
    if c.startswith("http://") or c.startswith("https://"):
        g = normalize_github_remote_url(c)
        if g:
            return g
        return normalize_generic_remote_url(c)
    g = normalize_github_remote_url(c)
    if g:
        return g
    return normalize_generic_remote_url(c)


def _github_repo_path_ssh_form(path_with_maybe_git: str) -> str:
    p = path_with_maybe_git.strip().removesuffix(".git").strip("/")
    return f"git@github.com:{p}.git"


def _https_to_ssh_github(https_canonical: str) -> Optional[str]:
    """``https://github.com/o/r`` -> ``git@github.com:o/r.git``."""
    g = normalize_github_remote_url(https_canonical)
    if not g:
        return None
    rest = (
        g.removeprefix("https://github.com/")
        .removeprefix("http://github.com/")
        .strip("/")
    )
    if not rest:
        return None
    return _github_repo_path_ssh_form(rest)


def _https_to_ssh_generic(https_url: str) -> Optional[str]:
    p = urlparse(https_url)
    if p.scheme not in ("http", "https") or not p.hostname or p.port:
        return None
    path = (p.path or "").strip("/")
    if not path:
        return None
    path = path.removesuffix(".git")
    host = p.hostname.lower()
    return f"git@{host}:{path}.git"


def to_preferred_ssh_remote_url(raw: str) -> Optional[str]:
    """Prefer ``git@host:path.git`` (or ``ssh://`` normalized to SCP-style) when the URL can be converted."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return None
    if raw.startswith("git@"):
        if raw.startswith("git@github.com:"):
            path = raw.split(":", 1)[1]
            return _github_repo_path_ssh_form(path)
        return raw
    if raw.startswith("ssh://"):
        p = urlparse(raw)
        host = p.hostname
        path = (p.path or "").strip("/").removesuffix(".git")
        user = p.username
        if not host or not path:
            return raw
        if user in (None, "git"):
            return f"git@{host}:{path}.git"
        return raw
    if normalize_github_remote_url(raw):
        ssh = _https_to_ssh_github(raw)
        if ssh:
            return ssh
    gen_https = normalize_generic_remote_url(raw)
    if gen_https:
        ssh = _https_to_ssh_generic(gen_https)
        if ssh:
            return ssh
    return raw


def normalize_generic_remote_url(raw: str) -> Optional[str]:
    """Map common clone URLs to an ``https://host/org/repo``-style URL (any host)."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return None
    if raw.startswith("git@"):
        m = re.match(r"^git@([^:]+):(.+)$", raw)
        if m:
            host, path = m.group(1), m.group(2)
            path = path.removesuffix(".git").strip("/")
            if host and path:
                return f"https://{host}/{path}"
        return None
    if raw.startswith("ssh://"):
        p = urlparse(raw)
        host = p.hostname
        if not host:
            return None
        path = (p.path or "").strip("/").removesuffix(".git")
        if not path:
            return f"https://{host}"
        return f"https://{host}/{path}"
    if raw.startswith("http://") or raw.startswith("https://"):
        p = urlparse(raw)
        host = p.hostname
        if not host:
            return None
        path = (p.path or "").strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        netloc = host
        if p.port:
            netloc = f"{host}:{p.port}"
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        if path:
            return urlunparse((scheme, netloc, "/" + path.replace("//", "/"), "", "", "")).rstrip(
                "/"
            )
        return f"{scheme}://{netloc}"
    return None


def classify_remote_kind_from_raw(raw: str) -> RemoteKind:
    """Heuristic: GitHub.com-style URLs use the GitHub identity path; everything else is generic."""
    if normalize_github_remote_url(raw):
        return "github"
    return "generic"


def infer_remote_kind_from_pinned_url(url: str) -> RemoteKind:
    """Infer ``remote_kind`` for YAML rows saved before that field existed."""
    if normalize_github_remote_url(url):
        return "github"
    return "generic"


def remote_identity_key(url: str, kind: RemoteKind) -> Optional[str]:
    """Stable string for comparing pinned vs checkout remotes."""
    if kind == "github":
        return github_remote_identity_key(url)
    u = (url or "").strip()
    if not u:
        return None
    gen = normalize_generic_remote_url(u)
    if gen:
        p = urlparse(gen)
        host = (p.hostname or "").lower()
        path = (p.path or "").strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if not host:
            return None
        return f"{host}/{path}" if path else host
    return None


def repo_web_url_for_path(path_str: str, remote_name: str = "origin") -> Optional[str]:
    """HTTPS browse URL for the configured remote (SSH remotes mapped for links; ``gh`` as fallback)."""
    root = Path(path_str).expanduser()
    raw = read_remote_url_via_git_cli(root, remote_name) or read_git_remote_raw_url(
        root, remote_name
    )
    if raw:
        return connection_url_to_browse_url(raw)
    gh_url = resolve_github_canonical_url_via_gh(root)
    return gh_url


def resolve_remote_pin_at_path(
    project_root: Path, remote_name: str = "origin"
) -> Tuple[Optional[str], Optional[str], RemoteKind]:
    """Compute ``(preferred_ssh_or_raw, identity_key, remote_kind)`` for pinning the checkout."""
    raw = read_remote_url_via_git_cli(project_root, remote_name) or read_git_remote_raw_url(
        project_root, remote_name
    )
    if not raw:
        return None, None, "generic"

    gh_url = resolve_github_canonical_url_via_gh(project_root)
    if gh_url:
        pin = to_preferred_ssh_remote_url(gh_url) or gh_url
        return pin, github_remote_identity_key(pin), "github"

    if normalize_github_remote_url(raw):
        pin = to_preferred_ssh_remote_url(raw) or raw.strip()
        return pin, github_remote_identity_key(pin), "github"

    gen = normalize_generic_remote_url(raw)
    if gen:
        pin = to_preferred_ssh_remote_url(raw) or raw.strip()
        return pin, remote_identity_key(pin, "generic"), "generic"

    pin = to_preferred_ssh_remote_url(raw)
    if pin:
        return pin, remote_identity_key(pin, "generic"), "generic"
    return None, None, "generic"


def compare_remote_pins(
    pinned_url: str,
    pinned_kind: RemoteKind,
    project_root: Path,
    remote_name: str,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (match, browse_url_for_message, actual_identity_key).

    ``browse_url_for_message`` is HTTPS when derivable (for UI copy); comparison uses SSH-preferred forms.
    """
    raw = read_remote_url_via_git_cli(project_root, remote_name) or read_git_remote_raw_url(
        project_root, remote_name
    )
    if not raw:
        return False, None, None

    gh_known = resolve_github_canonical_url_via_gh(project_root)
    is_github = bool(normalize_github_remote_url(raw)) or bool(gh_known)

    actual_conn = to_preferred_ssh_remote_url(raw) or raw.strip()
    if is_github:
        k_actual = github_remote_identity_key(actual_conn)
        browse = connection_url_to_browse_url(actual_conn) or normalize_github_remote_url(raw)
    else:
        k_actual = remote_identity_key(actual_conn, "generic")
        browse = connection_url_to_browse_url(actual_conn) or normalize_generic_remote_url(raw)

    if pinned_kind == "github":
        k_pin = github_remote_identity_key(pinned_url)
    else:
        k_pin = remote_identity_key(pinned_url, "generic")

    if not k_pin or not k_actual:
        eq = pinned_url.strip() == actual_conn.strip()
        return eq, browse or actual_conn, k_actual

    return k_pin == k_actual, browse or actual_conn, k_actual
