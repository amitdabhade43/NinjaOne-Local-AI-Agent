"""Progressively load only the NinjaOne skills relevant to a user request."""
from __future__ import annotations

import re
import sqlite3
import threading
from difflib import get_close_matches
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = DEFAULT_REPO_ROOT / "skills"
DEFAULT_AGENT_FILE = DEFAULT_REPO_ROOT / "agents" / "ninjaone-expert.md"
ALWAYS_LOAD = {"ninjaone-api-discovery"}
SYNONYMS = {
    "device": {"device", "devices"},
    "devices": {"device", "devices"},
    "technician": {"technician", "technicians"},
    "technicians": {"technician", "technicians"},
    "organization": {"organization", "organizations"},
    "organizations": {"organization", "organizations"},
    "user": {"user", "users"},
    "users": {"user", "users"},
    "location": {"location", "locations"},
    "locations": {"location", "locations"},
    "group": {"group", "groups"},
    "groups": {"group", "groups"},
    "machine": {"device", "devices"},
    "pc": {"device", "devices"},
    "pcs": {"device", "devices"},
    "desktop": {"device", "devices"},
    "desktops": {"device", "devices"},
    "machines": {"device", "devices"},
    "computer": {"device", "devices"},
    "computers": {"device", "devices"},
    "endpoint": {"device", "devices"},
    "endpoints": {"device", "devices"},
    "laptop": {"device", "devices"},
    "laptops": {"device", "devices"},
    "server": {"device", "devices"},
    "servers": {"device", "devices"},
    "offline": {"device", "devices", "status", "checkin"},
    "stale": {"device", "devices", "checkin", "checkins", "lastcontact", "lastseen"},
    "check": {"checkin", "checkins", "lastcontact", "lastseen"},
    "checks": {"checkin", "checkins", "lastcontact", "lastseen"},
    "checkin": {"checkin", "checkins", "device", "devices", "status"},
    "checkins": {"checkin", "checkins", "device", "devices", "status"},
    "reporting": {"device", "devices", "checkin", "status"},
    "reported": {"device", "devices", "checkin", "status"},
    "unreachable": {"device", "devices", "offline", "status"},
    "unresponsive": {"device", "devices", "offline", "status"},
    "disconnected": {"device", "devices", "offline", "status"},
    "org": {"organization", "organizations"},
    "orgs": {"organization", "organizations"},
    "customer": {"organization", "organizations"},
    "customers": {"organization", "organizations"},
    "site": {"location", "locations"},
    "sites": {"location", "locations"},
    "incident": {"alert", "alerts", "activity"},
    "incidents": {"alert", "alerts", "activity"},
    "problem": {"alert", "alerts", "issue"},
    "problems": {"alert", "alerts", "issue"},
    "repair": {"remediation", "operations"},
    "repairing": {"remediation", "operations"},
    "reboot": {"restart", "operations"},
    "restarting": {"restart", "operations"},
    "storage": {"disk", "disks"},
    "space": {"disk", "disks"},
    "updates": {"patch", "patches"},
    "hotfix": {"patch", "patches"},
    "employee": {"user", "users"},
    "staff": {"user", "users", "technician", "technicians"},
    "employees": {"user", "users"},
    "unhealthy": {"device", "devices", "health", "alerts"},
    "attention": {"alerts", "issues", "health", "devices"},
    "noncompliant": {"policy", "policies", "compliance"},
    "failed": {"failure", "failures", "patch", "patches", "alerts"},
    "broken": {"service", "services", "device", "devices"},
}
STOP_WORDS = {
    "a", "an", "and", "are", "all", "any", "can", "could", "do", "for",
    "from", "get", "give", "have", "how", "i", "in", "is", "it", "list",
    "me", "my", "of", "on", "our", "please", "show", "the", "to", "we",
    "what", "which", "with", "you", "your",
}


def _tokens(value: str) -> set[str]:
    tokens = {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in STOP_WORDS
    }
    expanded = set(tokens)
    for token in tokens:
        expanded.update(SYNONYMS.get(token, set()))
        # Correct only high-confidence misspellings of known domain vocabulary.
        if len(token) >= 5:
            near = get_close_matches(token, SYNONYMS.keys(), n=1, cutoff=0.88)
            if near:
                expanded.update(SYNONYMS.get(near[0], set()))
                expanded.add(near[0])
    return expanded


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
        name_match.group(1).strip().strip("'").strip('"') if name_match else "",
        description_match.group(1).strip().strip("'").strip('"') if description_match else "",
    )



# A small in-process FTS5 index keeps skill retrieval local and avoids rebuilding
# SQLite tables on every chat turn. The index is refreshed when any SKILL.md changes.
_INDEX_LOCK = threading.RLock()
_INDEX_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], sqlite3.Connection]] = {}


def _fts_skill_ranks(
    query_tokens: set[str],
    documents: list[tuple[Path, str, str, str]],
) -> dict[str, int]:
    """Return FTS5 relevance ranks (0 is best); gracefully fall back if FTS5 is absent."""
    if not query_tokens or not documents:
        return {}

    paths = [path for path, _, _, _ in documents]
    root_key = str(paths[0].parent.parent.resolve())
    signature = tuple(
        sorted(
            (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths
        )
    )
    # FTS query terms are quoted individually so punctuation cannot become syntax.
    match_query = " OR ".join(
        '"' + token.replace('"', '""') + '"'
        for token in sorted(query_tokens)
        if re.fullmatch(r"[a-z0-9_]+", token)
    )
    if not match_query:
        return {}

    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(root_key)
        if cached is None or cached[0] != signature:
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE skill_fts USING fts5("
                    "name, description, body, tokenize='unicode61')"
                )
                connection.executemany(
                    "INSERT INTO skill_fts(name, description, body) VALUES (?, ?, ?)",
                    [(name, description, markdown) for _, name, description, markdown in documents],
                )
            except sqlite3.OperationalError:
                connection.close()
                return {}
            if cached is not None:
                cached[1].close()
            _INDEX_CACHE[root_key] = (signature, connection)
        else:
            connection = cached[1]

        try:
            rows = connection.execute(
                "SELECT name FROM skill_fts "
                "WHERE skill_fts MATCH ? "
                "ORDER BY bm25(skill_fts, 8.0, 4.0, 1.0), name",
                (match_query,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}

    return {row[0]: rank for rank, row in enumerate(rows)}

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
    documents: list[tuple[Path, str, str, str]] = []
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError:
            continue
        name, description = _metadata(markdown)
        if name and description:
            documents.append((path, name, description, markdown))

    fts_ranks = _fts_skill_ranks(query_tokens, documents)
    ranked: list[tuple[int, str, str, str]] = []
    for _, name, description, markdown in documents:
        searchable = _tokens(f"{name.replace('-', ' ')} {description}")
        overlap = len(query_tokens & searchable)
        score = overlap * 3
        # FTS5 also searches the full skill body, helping queries whose wording
        # appears in operational guidance rather than the frontmatter summary.
        if name in fts_ranks:
            score += max(1, 12 - (fts_ranks[name] * 2))
        # Give the matching operational domain a clear ranking advantage.
        # Generic mentions of "device" inside other skills should not outrank
        # the dedicated inventory skill for machine/check-in questions.
        if name == "devices-inventory" and query_tokens & {
            "machine", "machines", "pc", "pcs", "computer", "computers",
            "endpoint", "endpoints", "laptop", "laptops", "server", "servers",
            "offline", "stale", "check", "checks", "checkin", "checkins",
            "lastcontact", "lastseen",
        }:
            score += 15
        if name == "organizations-locations" and query_tokens & {
            "customer", "customers", "org", "orgs", "organization",
            "organizations", "site", "sites", "branch", "branches",
        }:
            score += 15
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
