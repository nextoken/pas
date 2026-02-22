#!/usr/bin/env python3
"""
@pas-executable
Manage Agent Skills repos and symlinks.

Features:
- List installed skills in ~/.pas/skills (id + short description)
- Install a skill from a URL, using a sensible name:
  - Repo URL (clones the whole repo)
  - GitHub tree URL (snapshots a single subfolder from a multi-skill repo)
- Ensure common coding agent skill folders are linked:
  - ~/.claude/skills/<skill-name>
  - ~/.config/opencode/skills/<skill-name>
  - ~/.cursor/skills/<skill-name>
  - ~/.gemini/antigravity/skills/<skill-name>

Supported install URL forms:

1) Git repo URL (whole-repo clone)
   - Input examples:
     - git@github.com:PleasePrompto/notebooklm-skill.git
     - https://github.com/PleasePrompto/notebooklm-skill
   - Behavior:
     - HTTPS URLs are converted to SSH for cloning
     - Destination folder name is inferred (e.g. "notebooklm-skill" -> "notebooklm")

2) GitHub tree URL (single-skill snapshot from a subfolder)
   - Input example:
     - https://github.com/ComposioHQ/awesome-claude-skills/tree/master/document-skills/pdf
   - Behavior:
     - Uses git sparse-checkout to fetch only the selected subfolder path
     - Copies that folder into ~/.pas/skills/<skill-name> as a snapshot (no .git)
     - Default skill name is the last path segment (e.g. ".../document-skills/pdf" -> "pdf")

Startup behavior:
- Auto-repair missing/broken symlinks for all installed skills (non-interactive)
- Print a short report of what was fixed, or that no repair was needed
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import questionary
from rich.panel import Panel
from rich.table import Table

from helpers.core import (
    console,
    format_menu_choices,
    prompt_toolkit_menu,
    prompt_yes_no,
    run_command,
    safe_write_json,
)

# --------------------------
# --- Configuration Paths ---
PAS_SKILLS_DIR = Path.home() / ".pas" / "skills"
PAS_AGENT_SKILLS_CATALOG_PATH = Path.home() / ".pas" / "agent-skills.json"

CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
OPENCODE_SKILLS_DIR = Path.home() / ".config" / "opencode" / "skills"
CURSOR_SKILLS_DIR = Path.home() / ".cursor" / "skills"
GEMINI_ANTIGRAVITY_SKILLS_DIR = Path.home() / ".gemini" / "antigravity" / "skills"

AGENT_SKILLS_DIRS: List[Tuple[str, Path]] = [
    ("Claude", CLAUDE_SKILLS_DIR),
    ("OpenCode", OPENCODE_SKILLS_DIR),
    ("Cursor", CURSOR_SKILLS_DIR),
    ("Gemini", GEMINI_ANTIGRAVITY_SKILLS_DIR),
]
# --------------------------

# -----------------------------
# --- Naming / Parsing Rules ---
STRIP_REPO_SUFFIXES = (".git",)
STRIP_SKILL_SUFFIXES = ("-skill", "_skill", "-skills")
SKILL_NAME_ALLOWED_CHARS_RE = re.compile(r"[^a-zA-Z0-9._-]+")
DEFAULT_DESCRIPTION_WORDS = 10
DESCRIPTION_SCAN_LINES = 80
# -----------------------------


@dataclass(frozen=True)
class SkillInfo:
    skill_id: str
    path: Path
    description: str


@dataclass
class LinkOutcome:
    ok: int = 0
    created: int = 0
    fixed: int = 0
    skipped_conflict: int = 0

    def changed(self) -> int:
        return self.created + self.fixed


@dataclass(frozen=True)
class GitHubTreeTarget:
    repo_https: str  # https://github.com/owner/repo
    ref: str         # branch/tag/commit
    subdir: str      # repo-relative folder path


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _ensure_pas_dir() -> None:
    _ensure_dir(Path.home() / ".pas")


def _load_agent_skills_catalog() -> Dict[str, dict]:
    """
    Catalog format:
      {
        "version": 1,
        "skills": {
          "<skill_id>": {
            "source_url": "...",
            "source_type": "git_repo" | "github_tree",
            "repo_url": "...",   # for git_repo
            "tree": { "repo": "...", "ref": "...", "path": "..." },  # for github_tree
            "installed_at": "ISO8601"
          }
        }
      }
    """
    _ensure_pas_dir()
    if not PAS_AGENT_SKILLS_CATALOG_PATH.exists():
        return {"version": 1, "skills": {}}
    try:
        data = json.loads(PAS_AGENT_SKILLS_CATALOG_PATH.read_text(errors="ignore") or "{}")
        if not isinstance(data, dict):
            return {"version": 1, "skills": {}}
        if "skills" not in data or not isinstance(data.get("skills"), dict):
            data["skills"] = {}
        if "version" not in data:
            data["version"] = 1
        return data
    except Exception:
        return {"version": 1, "skills": {}}


def _save_agent_skills_catalog(data: Dict[str, dict]) -> None:
    _ensure_pas_dir()
    safe_write_json(PAS_AGENT_SKILLS_CATALOG_PATH, data, keep_backups=5, indent=2)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def catalog_record_install_git_repo(skill_id: str, *, source_url: str, repo_url: str) -> None:
    catalog = _load_agent_skills_catalog()
    catalog["skills"][skill_id] = {
        "source_url": source_url,
        "source_type": "git_repo",
        "repo_url": repo_url,
        "installed_at": _now_iso(),
    }
    _save_agent_skills_catalog(catalog)


def catalog_record_install_github_tree(skill_id: str, *, source_url: str, target: GitHubTreeTarget) -> None:
    catalog = _load_agent_skills_catalog()
    catalog["skills"][skill_id] = {
        "source_url": source_url,
        "source_type": "github_tree",
        "tree": {"repo": target.repo_https, "ref": target.ref, "path": target.subdir},
        "installed_at": _now_iso(),
    }
    _save_agent_skills_catalog(catalog)


def catalog_backfill_from_existing_skills(skills: List[SkillInfo]) -> Dict[str, int]:
    """
    Best-effort: for skills that are git repos, record their origin URL if missing.
    Returns counts for reporting: {"added": x, "skipped": y}.
    """
    catalog = _load_agent_skills_catalog()
    existing = catalog.get("skills", {})

    added = 0
    skipped = 0
    for s in skills:
        if s.skill_id in existing:
            continue
        if not (s.path / ".git").exists():
            skipped += 1
            continue
        res = run_command(["git", "-C", str(s.path), "remote", "get-url", "origin"])
        origin = (res.stdout or "").strip()
        if res.returncode == 0 and origin:
            existing[s.skill_id] = {
                "source_url": origin,
                "source_type": "git_repo",
                "repo_url": origin,
                "installed_at": _now_iso(),
            }
            added += 1
        else:
            skipped += 1

    catalog["skills"] = existing
    if added:
        _save_agent_skills_catalog(catalog)
    return {"added": added, "skipped": skipped}


def _sanitize_skill_name(name: str) -> str:
    cleaned = name.strip()
    cleaned = SKILL_NAME_ALLOWED_CHARS_RE.sub("-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "skill"


def _strip_suffixes(name: str) -> str:
    out = name
    for suf in STRIP_REPO_SUFFIXES:
        if out.endswith(suf):
            out = out[: -len(suf)]
    for suf in STRIP_SKILL_SUFFIXES:
        if out.endswith(suf):
            out = out[: -len(suf)]
    return out


def infer_skill_name_from_url(repo_url: str) -> str:
    """
    Infer a skill name from repo URL.
    Examples:
    - git@github.com:PleasePrompto/notebooklm-skill.git -> notebooklm
    - https://github.com/PleasePrompto/notebooklm-skill -> notebooklm
    """
    raw = repo_url.strip()

    # SSH scp-like URL: git@host:owner/repo(.git)
    if "@" in raw and ":" in raw and not raw.startswith(("http://", "https://")):
        last = raw.split(":")[-1].rstrip("/").split("/")[-1]
        return _sanitize_skill_name(_strip_suffixes(last))

    # HTTP(S) URL
    try:
        parsed = urlparse(raw)
        last = (parsed.path or "").rstrip("/").split("/")[-1]
        return _sanitize_skill_name(_strip_suffixes(last))
    except Exception:
        # Fallback: best-effort
        last = raw.rstrip("/").split("/")[-1]
        return _sanitize_skill_name(_strip_suffixes(last))


def parse_github_tree_url(url: str) -> Optional[GitHubTreeTarget]:
    """
    Parse a GitHub tree URL like:
      https://github.com/owner/repo/tree/<ref>/<path>
    Returns None if not a supported tree URL.
    """
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        return None

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    if parsed.netloc.lower() != "github.com":
        return None

    parts = [p for p in (parsed.path or "").split("/") if p]
    # Expect: owner/repo/tree/ref/path...
    if len(parts) < 5:
        return None
    owner, repo, tree_kw, ref = parts[0], parts[1], parts[2], parts[3]
    if tree_kw != "tree":
        return None
    subdir = "/".join(parts[4:])
    if not owner or not repo or not ref or not subdir:
        return None

    repo_https = f"https://github.com/{owner}/{repo}"
    return GitHubTreeTarget(repo_https=repo_https, ref=ref, subdir=subdir)


def normalize_repo_url_to_ssh(repo_url: str) -> str:
    """
    Convert HTTPS repo URLs to SSH form.
    - https://github.com/owner/repo(.git) -> git@github.com:owner/repo.git
    If already SSH-like (git@host:...), returns as-is.
    """
    raw = repo_url.strip()
    if raw.startswith("git@") or raw.startswith("ssh://"):
        return raw

    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        host = parsed.netloc
        path = (parsed.path or "").lstrip("/").rstrip("/")
        if not host or not path:
            return raw
        if not path.endswith(".git"):
            path = f"{path}.git"
        return f"git@{host}:{path}"

    # Handle github.com/owner/repo without scheme
    if raw.startswith("github.com/"):
        path = raw[len("github.com/") :].lstrip("/").rstrip("/")
        if not path.endswith(".git"):
            path = f"{path}.git"
        return f"git@github.com:{path}"

    return raw


def _first_meaningful_line(lines: Iterable[str]) -> Optional[str]:
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        # Skip common frontmatter separators
        if s in ("---", "```"):
            continue
        return s
    return None


def _clean_description(text: str) -> str:
    s = text.strip()
    # Prefer markdown headings: "# Title" -> "Title"
    s = re.sub(r"^#{1,6}\s+", "", s)
    # Remove markdown emphasis/backticks
    s = s.replace("`", "").replace("*", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _shorten_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def extract_skill_description(skill_dir: Path) -> str:
    candidates = [skill_dir / "SKILL.md", skill_dir / "README.md", skill_dir / "README.txt"]
    for p in candidates:
        if not p.exists() or not p.is_file():
            continue
        try:
            raw_lines = p.read_text(errors="ignore").splitlines()[:DESCRIPTION_SCAN_LINES]
            # Prefer first markdown heading if available
            heading = next((ln for ln in raw_lines if ln.strip().startswith("#")), None)
            line = heading or _first_meaningful_line(raw_lines)
            if not line:
                continue
            cleaned = _clean_description(line)
            cleaned = _shorten_words(cleaned, DEFAULT_DESCRIPTION_WORDS)
            return cleaned or "No description"
        except Exception:
            continue
    return "No description"


def list_installed_skills() -> List[SkillInfo]:
    if not PAS_SKILLS_DIR.exists():
        return []

    skills: List[SkillInfo] = []
    for p in sorted(PAS_SKILLS_DIR.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        desc = extract_skill_description(p)
        skills.append(SkillInfo(skill_id=p.name, path=p, description=desc))
    return skills


def _symlink_points_to(link_path: Path, target_dir: Path) -> bool:
    if not link_path.is_symlink():
        return False
    try:
        # resolve(strict=False) handles broken symlinks; we still compare best-effort
        return link_path.resolve().samefile(target_dir.resolve())
    except Exception:
        # Fallback: compare resolved strings
        try:
            return str(link_path.resolve()) == str(target_dir.resolve())
        except Exception:
            return False


def _link_status(link_path: Path, target_dir: Path) -> str:
    """
    Return one of: ok, missing, broken, wrong_symlink, conflict
    """
    if link_path.is_symlink():
        if not link_path.exists():
            return "broken"
        return "ok" if _symlink_points_to(link_path, target_dir) else "wrong_symlink"
    if link_path.exists():
        return "conflict"
    return "missing"


def _remove_path_safely(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.exists() and path.is_dir():
        shutil.rmtree(path)


def ensure_skill_links(
    skill: SkillInfo,
    *,
    interactive_conflicts: bool,
    auto_fix_broken: bool,
) -> Dict[str, LinkOutcome]:
    outcomes: Dict[str, LinkOutcome] = {label: LinkOutcome() for label, _ in AGENT_SKILLS_DIRS}
    target = skill.path

    for label, base_dir in AGENT_SKILLS_DIRS:
        _ensure_dir(base_dir)
        link_path = base_dir / skill.skill_id
        status = _link_status(link_path, target)

        if status == "ok":
            outcomes[label].ok += 1
            continue

        if status in ("missing", "broken"):
            if status == "broken" and auto_fix_broken:
                try:
                    link_path.unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    link_path.symlink_to(target)
                    outcomes[label].fixed += 1
                except Exception:
                    outcomes[label].skipped_conflict += 1
                continue

            if status == "missing":
                try:
                    link_path.symlink_to(target)
                    outcomes[label].created += 1
                except Exception:
                    outcomes[label].skipped_conflict += 1
                continue

        # wrong_symlink or conflict
        if not interactive_conflicts:
            outcomes[label].skipped_conflict += 1
            continue

        # Interactive conflict resolution
        conflict_title = f"{label} link conflict for '{skill.skill_id}'"
        detail = f"Destination exists at: {link_path}\nExpected target: {target}"
        console.print(Panel(detail, title=conflict_title, border_style="yellow"))

        action_choices = [
            {"title": "Overwrite destination (remove existing and relink)", "value": "overwrite"},
            {"title": "Skip", "value": "skip"},
            {"title": "[Back]", "value": "back"},
        ]
        selected = prompt_toolkit_menu(format_menu_choices(action_choices, title_field="title", value_field="value"))
        if not selected or selected == "back" or selected == "skip":
            outcomes[label].skipped_conflict += 1
            continue

        if selected == "overwrite":
            # If it's a real directory, double-confirm
            if link_path.exists() and not link_path.is_symlink() and link_path.is_dir():
                if not prompt_yes_no(f"{link_path} is a real directory. Remove it to relink?", default=False):
                    outcomes[label].skipped_conflict += 1
                    continue
            _remove_path_safely(link_path)
            try:
                link_path.symlink_to(target)
                outcomes[label].fixed += 1
            except Exception:
                outcomes[label].skipped_conflict += 1

    return outcomes


def _summarize_link_outcomes(per_label: Dict[str, LinkOutcome]) -> Tuple[int, int, int, int]:
    ok = sum(o.ok for o in per_label.values())
    created = sum(o.created for o in per_label.values())
    fixed = sum(o.fixed for o in per_label.values())
    skipped = sum(o.skipped_conflict for o in per_label.values())
    return ok, created, fixed, skipped


def auto_repair_all_skills_and_report() -> None:
    _ensure_dir(PAS_SKILLS_DIR)
    for _, d in AGENT_SKILLS_DIRS:
        _ensure_dir(d)

    skills = list_installed_skills()
    if not skills:
        console.print(Panel("No skills found in ~/.pas/skills.", title="skills-ops", border_style="cyan"))
        return

    totals: Dict[str, LinkOutcome] = {label: LinkOutcome() for label, _ in AGENT_SKILLS_DIRS}

    for s in skills:
        per_label = ensure_skill_links(s, interactive_conflicts=False, auto_fix_broken=True)
        for label, outcome in per_label.items():
            totals[label].ok += outcome.ok
            totals[label].created += outcome.created
            totals[label].fixed += outcome.fixed
            totals[label].skipped_conflict += outcome.skipped_conflict

    ok, created, fixed, skipped = _summarize_link_outcomes(totals)
    changed = created + fixed

    if changed == 0 and skipped == 0:
        console.print(
            Panel(
                "No auto-repair required.\nAll skills are already correctly linked for Claude/Cursor/OpenCode.",
                title="skills-ops startup",
                border_style="green",
            )
        )
        return

    lines: List[str] = []
    lines.append(f"Skills scanned: {len(skills)}")
    lines.append(f"Links created: {created}")
    lines.append(f"Links fixed (broken): {fixed}")
    if skipped:
        lines.append(f"Conflicts skipped (needs interactive repair): {skipped}")

    # Per-agent breakdown (only show if something happened)
    lines.append("")
    for label in [l for l, _ in AGENT_SKILLS_DIRS]:
        o = totals[label]
        lines.append(f"- {label}: created {o.created}, fixed {o.fixed}, skipped {o.skipped_conflict}")

    border = "yellow" if skipped else "cyan"
    console.print(Panel("\n".join(lines).strip(), title="skills-ops startup", border_style=border))


def _print_capability_summary() -> None:
    info_text = f"""
[bold]skills-ops[/bold]

Manages Agent Skills repos under:
- [cyan]{PAS_SKILLS_DIR}[/cyan]

Ensures each skill is linked into common coding agents:
- [cyan]{CLAUDE_SKILLS_DIR}[/cyan]
- [cyan]{OPENCODE_SKILLS_DIR}[/cyan]
- [cyan]{CURSOR_SKILLS_DIR}[/cyan]
- [cyan]{GEMINI_ANTIGRAVITY_SKILLS_DIR}[/cyan]

Startup auto-repair:
- Fixes missing/broken links automatically
- Skips conflicts and reports them (use interactive repair to resolve)
""".strip()
    console.print(Panel(info_text, title="skills-ops", border_style="blue"))


def _detect_name_conflicts(skill_name: str) -> List[str]:
    conflicts: List[str] = []
    dest_dir = PAS_SKILLS_DIR / skill_name
    if dest_dir.exists() or dest_dir.is_symlink():
        conflicts.append(f"Skill folder already exists: {dest_dir}")
    for label, base in AGENT_SKILLS_DIRS:
        p = base / skill_name
        if p.exists() or p.is_symlink():
            conflicts.append(f"{label} destination already exists: {p}")
    return conflicts


def _choose_unique_skill_name(initial: str) -> Optional[str]:
    name = _sanitize_skill_name(initial)
    while True:
        conflicts = _detect_name_conflicts(name)
        if not conflicts:
            return name

        console.print(
            Panel(
                "\n".join(conflicts),
                title=f"Name '{name}' already exists",
                border_style="yellow",
            )
        )
        choices = [
            {"title": "Rename (choose a different skill name)", "value": "rename"},
            {"title": "Overwrite (remove existing destinations)", "value": "overwrite"},
            {"title": "[Back]", "value": "back"},
        ]
        action = prompt_toolkit_menu(format_menu_choices(choices, title_field="title", value_field="value"))
        if not action or action == "back":
            return None
        if action == "rename":
            new_name = questionary.text("Enter new skill name:", default=name).ask()
            if not new_name:
                continue
            name = _sanitize_skill_name(new_name)
            continue
        if action == "overwrite":
            # Confirm destructive action once
            if not prompt_yes_no("Overwrite existing skill folder/links with this name?", default=False):
                continue
            _remove_path_safely(PAS_SKILLS_DIR / name)
            for _, base in AGENT_SKILLS_DIRS:
                _remove_path_safely(base / name)
            return name


def clone_skill_repo(ssh_url: str, skill_name: str) -> Optional[SkillInfo]:
    _ensure_dir(PAS_SKILLS_DIR)
    dest_dir = PAS_SKILLS_DIR / skill_name

    console.print(Panel(f"Cloning:\n- URL: {ssh_url}\n- Into: {dest_dir}", title="Clone skill", border_style="cyan"))
    res = run_command(["git", "clone", ssh_url, str(dest_dir)])
    if res.returncode != 0:
        stderr = (res.stderr or "").strip()
        msg = stderr or "git clone failed."
        console.print(Panel(msg, title="Clone failed", border_style="red"))
        return None

    desc = extract_skill_description(dest_dir)
    return SkillInfo(skill_id=skill_name, path=dest_dir, description=desc)


def snapshot_skill_from_github_tree(
    target: GitHubTreeTarget,
    *,
    skill_name: str,
) -> Optional[SkillInfo]:
    """
    Use sparse-checkout to fetch only the tree subdir and snapshot it into ~/.pas/skills/<skill_name>.
    """
    _ensure_dir(PAS_SKILLS_DIR)
    dest_dir = PAS_SKILLS_DIR / skill_name

    ssh_repo = normalize_repo_url_to_ssh(target.repo_https)
    console.print(
        Panel(
            f"Installing from GitHub tree URL:\n"
            f"- Repo: {target.repo_https}\n"
            f"- Ref:  {target.ref}\n"
            f"- Path: {target.subdir}\n\n"
            f"Snapshot into:\n- {dest_dir}",
            title="Install skill (tree URL)",
            border_style="cyan",
        )
    )

    with tempfile.TemporaryDirectory(prefix="pas-skill-") as td:
        tmp_root = Path(td)

        res = run_command(["git", "clone", "--filter=blob:none", "--no-checkout", ssh_repo, str(tmp_root)])
        if res.returncode != 0:
            msg = (res.stderr or "").strip() or "git clone failed."
            console.print(Panel(msg, title="Clone failed", border_style="red"))
            return None

        res = run_command(["git", "-C", str(tmp_root), "sparse-checkout", "init", "--cone"])
        if res.returncode != 0:
            msg = (res.stderr or "").strip() or "git sparse-checkout init failed."
            console.print(Panel(msg, title="Sparse checkout failed", border_style="red"))
            return None

        res = run_command(["git", "-C", str(tmp_root), "sparse-checkout", "set", target.subdir])
        if res.returncode != 0:
            msg = (res.stderr or "").strip() or "git sparse-checkout set failed."
            console.print(Panel(msg, title="Sparse checkout failed", border_style="red"))
            return None

        res = run_command(["git", "-C", str(tmp_root), "checkout", target.ref])
        if res.returncode != 0:
            msg = (res.stderr or "").strip() or f"git checkout '{target.ref}' failed."
            console.print(Panel(msg, title="Checkout failed", border_style="red"))
            return None

        src = tmp_root / target.subdir
        if not src.exists() or not src.is_dir():
            console.print(
                Panel(
                    f"Checked out, but folder not found:\n{src}",
                    title="Install failed",
                    border_style="red",
                )
            )
            return None

        # Copy the selected folder only (snapshot). dest_dir must not exist.
        try:
            shutil.copytree(src, dest_dir)
        except Exception as e:
            console.print(Panel(str(e), title="Snapshot copy failed", border_style="red"))
            return None

    desc = extract_skill_description(dest_dir)
    return SkillInfo(skill_id=skill_name, path=dest_dir, description=desc)


def install_skill_from_repo_url(repo_url: str, *, interactive: bool) -> bool:
    raw_url = repo_url.strip()
    if not raw_url:
        console.print(Panel("Repo URL is empty.", title="Install failed", border_style="red"))
        return False

    tree_target = parse_github_tree_url(raw_url)
    inferred = infer_skill_name_from_url(raw_url)

    # Tree URL: install the selected folder via sparse-checkout snapshot.
    if tree_target:
        if interactive:
            proposed_name = questionary.text("Skill name:", default=inferred).ask() or inferred
            chosen = _choose_unique_skill_name(proposed_name)
            if not chosen:
                return False
            info = snapshot_skill_from_github_tree(tree_target, skill_name=chosen)
            if not info:
                return False
            catalog_record_install_github_tree(info.skill_id, source_url=raw_url, target=tree_target)
            outcomes = ensure_skill_links(info, interactive_conflicts=True, auto_fix_broken=True)
            _, created, fixed, skipped = _summarize_link_outcomes(outcomes)
            console.print(
                Panel(
                    f"Installed: {info.skill_id}\n"
                    f"Description: {info.description}\n\n"
                    f"Links created: {created}\n"
                    f"Links fixed: {fixed}\n"
                    f"Conflicts skipped: {skipped}",
                    title="Install complete",
                    border_style="green" if skipped == 0 else "yellow",
                )
            )
            return True

        # Non-interactive tree install
        conflicts = _detect_name_conflicts(inferred)
        if conflicts:
            console.print(
                Panel(
                    "\n".join(conflicts),
                    title=f"Cannot auto-install '{inferred}' (name conflicts)",
                    border_style="red",
                )
            )
            return False

        info = snapshot_skill_from_github_tree(tree_target, skill_name=inferred)
        if not info:
            return False

        catalog_record_install_github_tree(info.skill_id, source_url=raw_url, target=tree_target)
        outcomes = ensure_skill_links(info, interactive_conflicts=False, auto_fix_broken=True)
        _, created, fixed, skipped = _summarize_link_outcomes(outcomes)
        border = "yellow" if skipped else "green"
        console.print(
            Panel(
                f"Installed: {info.skill_id}\n"
                f"Description: {info.description}\n\n"
                f"Links created: {created}\n"
                f"Links fixed: {fixed}\n"
                f"Conflicts skipped: {skipped}",
                title="Auto-install complete",
                border_style=border,
            )
        )
        return True

    # Plain repo URL: clone whole repo.
    ssh_url = normalize_repo_url_to_ssh(raw_url)

    if interactive:
        proposed_name = questionary.text("Skill name:", default=inferred).ask() or inferred
        chosen = _choose_unique_skill_name(proposed_name)
        if not chosen:
            return False
        info = clone_skill_repo(ssh_url, chosen)
        if not info:
            return False
        catalog_record_install_git_repo(info.skill_id, source_url=raw_url, repo_url=ssh_url)
        outcomes = ensure_skill_links(info, interactive_conflicts=True, auto_fix_broken=True)
        _, created, fixed, skipped = _summarize_link_outcomes(outcomes)
        console.print(
            Panel(
                f"Installed: {info.skill_id}\n"
                f"Description: {info.description}\n\n"
                f"Links created: {created}\n"
                f"Links fixed: {fixed}\n"
                f"Conflicts skipped: {skipped}",
                title="Install complete",
                border_style="green" if skipped == 0 else "yellow",
            )
        )
        return True

    # Non-interactive install (best-effort)
    conflicts = _detect_name_conflicts(inferred)
    if conflicts:
        console.print(
            Panel(
                "\n".join(conflicts),
                title=f"Cannot auto-install '{inferred}' (name conflicts)",
                border_style="red",
            )
        )
        return False

    info = clone_skill_repo(ssh_url, inferred)
    if not info:
        return False

    catalog_record_install_git_repo(info.skill_id, source_url=raw_url, repo_url=ssh_url)
    outcomes = ensure_skill_links(info, interactive_conflicts=False, auto_fix_broken=True)
    _, created, fixed, skipped = _summarize_link_outcomes(outcomes)
    border = "yellow" if skipped else "green"
    console.print(
        Panel(
            f"Installed: {info.skill_id}\n"
            f"Description: {info.description}\n\n"
            f"Links created: {created}\n"
            f"Links fixed: {fixed}\n"
            f"Conflicts skipped: {skipped}",
            title="Auto-install complete",
            border_style=border,
        )
    )
    return True


def install_skill_flow() -> None:
    raw_url = questionary.text(
        "Enter skills repo URL (GitHub tree URL supported; HTTPS will be converted to SSH):",
    ).ask()
    if not raw_url:
        return

    inferred = infer_skill_name_from_url(raw_url)
    proposed_name = questionary.text("Skill name:", default=inferred).ask() or inferred
    chosen = _choose_unique_skill_name(proposed_name)
    if not chosen:
        return

    tree_target = parse_github_tree_url(raw_url)
    if tree_target:
        info = snapshot_skill_from_github_tree(tree_target, skill_name=chosen)
    else:
        ssh_url = normalize_repo_url_to_ssh(raw_url)
        info = clone_skill_repo(ssh_url, chosen)

    if not info:
        return
    if tree_target:
        catalog_record_install_github_tree(info.skill_id, source_url=raw_url, target=tree_target)
    else:
        catalog_record_install_git_repo(info.skill_id, source_url=raw_url, repo_url=normalize_repo_url_to_ssh(raw_url))

    outcomes = ensure_skill_links(info, interactive_conflicts=True, auto_fix_broken=True)
    _, created, fixed, skipped = _summarize_link_outcomes(outcomes)
    console.print(
        Panel(
            f"Installed: {info.skill_id}\n"
            f"Description: {info.description}\n\n"
            f"Links created: {created}\n"
            f"Links fixed: {fixed}\n"
            f"Conflicts skipped: {skipped}",
            title="Install complete",
            border_style="green" if skipped == 0 else "yellow",
        )
    )


def list_skills_flow() -> None:
    skills = list_installed_skills()
    if not skills:
        console.print(Panel("No skills found in ~/.pas/skills.", title="Installed skills", border_style="cyan"))
        return

    table = Table(title="Installed skills", show_lines=False)
    table.add_column("Skill ID", style="bold")
    table.add_column("Description")
    for label, _ in AGENT_SKILLS_DIRS:
        table.add_column(label, justify="center")

    for s in skills:
        statuses: List[str] = []
        for _, base in AGENT_SKILLS_DIRS:
            lp = base / s.skill_id
            st = _link_status(lp, s.path)
            statuses.append("✓" if st == "ok" else ("!" if st in ("wrong_symlink", "conflict") else "·"))
        table.add_row(s.skill_id, s.description, *statuses)

    console.print(table)


def interactive_repair_flow() -> None:
    skills = list_installed_skills()
    if not skills:
        console.print(Panel("No skills found in ~/.pas/skills.", title="Repair", border_style="cyan"))
        return

    items = [{"title": f"{s.skill_id} — {s.description}", "value": s.skill_id} for s in skills]
    items.append({"title": "[Back]", "value": "back"})
    items.append({"title": "[Quit]", "value": "quit"})

    selected = prompt_toolkit_menu(format_menu_choices(items, title_field="title", value_field="value"))
    if not selected or selected in ("back", "quit"):
        return

    skill = next((s for s in skills if s.skill_id == selected), None)
    if not skill:
        return

    outcomes = ensure_skill_links(skill, interactive_conflicts=True, auto_fix_broken=True)
    ok, created, fixed, skipped = _summarize_link_outcomes(outcomes)
    console.print(
        Panel(
            f"Skill: {skill.skill_id}\n\n"
            f"Links created: {created}\n"
            f"Links fixed: {fixed}\n"
            f"Conflicts skipped: {skipped}\n"
            f"Already OK: {ok}",
            title="Repair results",
            border_style="green" if skipped == 0 else "yellow",
        )
    )


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "repo_url",
        nargs="?",
        help="If provided, auto-install this skill repo URL then exit.",
    )
    parser.add_argument(
        "--no-auto-repair",
        action="store_true",
        help="Skip the startup auto-repair pass for missing/broken links.",
    )
    parser.add_argument(
        "--no-menu",
        action="store_true",
        help="Run startup checks then exit (non-interactive).",
    )
    args = parser.parse_args(argv)

    _print_capability_summary()
    if not args.no_auto_repair:
        auto_repair_all_skills_and_report()
        # Best-effort backfill of the catalog for git-based skills (origin remote).
        skills = list_installed_skills()
        catalog_backfill_from_existing_skills(skills)

    if args.repo_url:
        ok = install_skill_from_repo_url(args.repo_url, interactive=sys.stdin.isatty())
        if not ok:
            raise SystemExit(1)
        return

    # If we're not in a real terminal, don't try to render interactive menus.
    if args.no_menu or not sys.stdin.isatty():
        return

    while True:
        menu_items = [
            {"title": "List installed skills", "value": "list"},
            {"title": "Install skill from git repo", "value": "install"},
            {"title": "Repair/relink skills (interactive)", "value": "repair"},
            {"title": "[Quit]", "value": "quit"},
        ]

        selection = prompt_toolkit_menu(format_menu_choices(menu_items, title_field="title", value_field="value"))
        if not selection or selection == "quit":
            return

        if selection == "list":
            list_skills_flow()
        elif selection == "install":
            install_skill_flow()
        elif selection == "repair":
            interactive_repair_flow()


if __name__ == "__main__":
    main()

