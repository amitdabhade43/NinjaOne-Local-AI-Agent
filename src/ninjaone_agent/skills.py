"""Progressively load only the NinjaOne skills relevant to a user request."""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = DEFAULT_REPO_ROOT / "skills"
DEFAULT_AGENT_FILE = DEFAULT_REPO_ROOT / "agents" / "ninjaone-expert.md"
ALWAYS_LOAD = {"ninjaone-api-discovery"}
STOP_WORDS = {
    "a", "an", "and", "are", "all", "any", "can", "could", "do", "for",
    "from", "get", "give", "have", "how", "i", "in", "is", "it", "list",
    "me", "my", "of", "on", "our", "please", "show", "the", "to", "we",
    "what", "which", "with", "you", "your",
}


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in STOP_WORDS
    }


def _metadata(markdown: str) -> tuple[str, str]:
    """Read the small Agent Skills frontmatter without a YAML dependency."""
    if not markdown.startswith("---"):
        return "", ""
    end = markdown.find("\n---", 3)
    if end < 0:
        return "", ""
    frontmatter = markdown[3:end]
    name_match = re.search(r"(?m)^name:\s*([^\n]+)", frontmatter)
    description_match = re.search(r"(?m)^description:\s*(.+)", frontmatter)
    return (
        name_match.group(1).strip().strip("'\\"") if name_match else "",
        description_match.group(1).strip().strip("'\\"") if description_match else "",
    )


def load_relevant_skills(
    query: str,
    skills_dir: Path | str | None = None,
    *,
    max_skills: int = 3,
    max_chars: int = 16000,
) -> list[dict[str, str]]:
    """Return the highest-scoring skills; the API discovery skill is always included."""
    root = Path(skills_dir) if skills_dir is not None else DEFAULT_SKILLS_DIR
    if not root.is_dir():
        return []

    query_tokens = _tokens(query)
    ranked: list[tuple[int, str, str, str]] = []
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError:
            continue
        name, description = _metadata(markdown)
        if not name or not description:
            continue
        searchable = _tokens(f"{name.replace('-', ' ')} {description}")
        overlap = len(query_tokens & searchable)
        score = overlap * 3
        if name in ALWAYS_LOAD:
            score += 1000
        if score > 0:
            ranked.append((score, name, description, markdown))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[:max(1, max_skills)]
    results: list[dict[str, str]] = []
    remaining = max_chars
    for _, name, description, markdown in selected:
        if remaining <= 0:
            break
        body = markdown[:remaining]
        results.append({"name": name, "description": description, "content": body})
        remaining -= len(body)
    return results


def render_master_instructions(agent_file: Path | str | None = None) -> str:
    """Load the master agent policy file, if present."""
    path = Path(agent_file) if agent_file is not None else DEFAULT_AGENT_FILE
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def render_skill_context(query: str, skills_dir: Path | str | None = None) -> str:
    skills = load_relevant_skills(query, skills_dir)
    if not skills:
        return ""
    sections = ["Use the following relevant NinjaOne skills as operational guidance:"]
    for skill in skills:
        sections.append(f"\n--- SKILL: {skill['name']} ---\n{skill['content']}")
    return "\n".join(sections)
