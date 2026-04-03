import re
from pathlib import Path
from typing import Optional

def _github_url_from_path(path_str: str) -> Optional[str]:
    """Read .git/config origin URL (GitHub only), supporting submodules/worktrees."""
    root = Path(path_str)
    
    def _git_dir_for_project(project_root: Path) -> Optional[Path]:
        git = project_root / ".git"
        if git.is_dir(): return git
        if git.is_file():
            try:
                first = git.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                if first.startswith("gitdir:"):
                    rel = first.split(":", 1)[1].strip()
                    resolved = (project_root / rel).resolve()
                    if resolved.is_dir(): return resolved
            except OSError: pass
        return None

    def _normalize_github_remote_url(raw: str) -> Optional[str]:
        raw = raw.strip().strip('"').strip("'")
        if not raw or "github.com" not in raw: return None
        if raw.startswith("git@github.com:"):
            repo = raw.split(":", 1)[1].removesuffix(".git").strip()
            return f"https://github.com/{repo}"
        if raw.startswith("ssh://git@github.com/"):
            rest = raw.removeprefix("ssh://git@github.com/").removesuffix(".git").strip()
            return f"https://github.com/{rest}"
        if raw.startswith("https://github.com/") or raw.startswith("http://github.com/"):
            u = raw.split("?", 1)[0].rstrip("/")
            if u.endswith(".git"): u = u[:-4]
            return u
        return None

    git_dir = _git_dir_for_project(root)
    if not git_dir: return None
    cfg = git_dir / "config"
    if not cfg.is_file(): return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
        in_origin = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_origin = stripped == '[remote "origin"]' or (
                    stripped.startswith("[remote") and '"origin"' in stripped
                )
                continue
            if in_origin:
                m = re.match(r"url\s*=\s*(.+)$", stripped)
                if m: return _normalize_github_remote_url(m.group(1))
    except OSError: pass
    return None
