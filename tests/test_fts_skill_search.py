from ninjaone_agent.skills import load_relevant_skills


def test_fts_searches_skill_body_not_only_frontmatter(tmp_path):
    skill_dir = tmp_path / "skills"
    device_skill = skill_dir / "devices-inventory"
    other_skill = skill_dir / "patch-management"
    device_skill.mkdir(parents=True)
    other_skill.mkdir(parents=True)

    (device_skill / "SKILL.md").write_text(
        "---\n"
        "name: devices-inventory\n"
        "description: General endpoint inventory guidance.\n"
        "---\n"
        "When an administrator needs to recover a BitLocker recovery escrow "
        "record, inspect the endpoint's encryption metadata.\n",
        encoding="utf-8",
    )
    (other_skill / "SKILL.md").write_text(
        "---\n"
        "name: patch-management\n"
        "description: General software update guidance.\n"
        "---\n"
        "Review patch deployment schedules and reboot windows.\n",
        encoding="utf-8",
    )

    results = load_relevant_skills(
        "BitLocker recovery escrow",
        skills_dir=skill_dir,
        max_skills=2,
    )

    assert results
    assert results[0]["name"] == "devices-inventory"


def test_fts_router_keeps_fuzzy_machine_and_customer_aliases():
    machine_skills = {
        item["name"]
        for item in load_relevant_skills("Which machines have stale check-ins?")
    }
    customer_skills = {
        item["name"]
        for item in load_relevant_skills("Show all customers and sites")
    }

    assert "devices-inventory" in machine_skills
    assert "organizations-locations" in customer_skills
