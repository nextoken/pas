import re
from pathlib import Path
from typing import Optional


def _git_dir_for_project(project_root: Path) -> Optional[Path]:
    """Resolve the real ``.git`` directory (handles submodules and worktrees)."""
    git = project_root / ".git"
    if git.is_dir():
        return git
    if git.is_file():
        try:
            first = git.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            if first.startswith("gitdir:"):
                rel = first.split(":", 1)[1].strip()
                resolved = (project_root / rel).resolve()
                if resolved.is_dir():
                    return resolved
        except OSError:
            pass
    return None


def read_git_remote_raw_url(project_root: Path, remote_name: str = "origin") -> Optional[str]:
    """Return the raw ``url =`` value for ``[remote "<name>"]`` from ``.git/config``."""
    name = (remote_name or "origin").strip() or "origin"
    git_dir = _git_dir_for_project(project_root)
    if not git_dir:
        return None
    cfg = git_dir / "config"
    if not cfg.is_file():
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    target = f'[remote "{name}"]'
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == target or (
                stripped.startswith("[remote") and f'"{name}"' in stripped
            )
            continue
        if in_section:
            m = re.match(r"url\s*=\s*(.+)$", stripped)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def normalize_github_remote_url(raw: str) -> Optional[str]:
    """Map a GitHub clone URL to ``https://github.com/org/repo`` (no ``.git``), or None if not GitHub."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw or "github.com" not in raw:
        return None
    if raw.startswith("git@github.com:"):
        repo = raw.split(":", 1)[1].removesuffix(".git").strip()
        return f"https://github.com/{repo}"
    if raw.startswith("ssh://git@github.com/"):
        rest = raw.removeprefix("ssh://git@github.com/").removesuffix(".git").strip()
        return f"https://github.com/{rest}"
    if raw.startswith("https://github.com/") or raw.startswith("http://github.com/"):
        u = raw.split("?", 1)[0].rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        return u
    return None


def github_remote_identity_key(url: str) -> Optional[str]:
    """Stable lowercase key for comparing two GitHub remotes (https browse URL or raw clone URL)."""
    https = normalize_github_remote_url(url)
    if not https:
        return None
    u = https.lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u


def github_url_for_path(path_str: str, remote_name: str = "origin") -> Optional[str]:
    """Repo web URL for a working tree (GitHub via ``gh`` when available; any host via generic normalize)."""
    from .git_remote_providers import repo_web_url_for_path

    return repo_web_url_for_path(path_str, remote_name)


def _github_url_from_path(path_str: str) -> Optional[str]:
    """Read ``origin`` URL (GitHub only); supports submodules/worktrees."""
    return github_url_for_path(path_str, "origin")
