from ninjaone_agent.skills import load_relevant_skills, render_skill_context


def test_always_loads_api_discovery_and_selects_device_skill():
    skills = load_relevant_skills("Which laptops have low disk space?")
    names = {skill["name"] for skill in skills}
    assert "ninjaone-api-discovery" in names
    assert "devices-inventory" in names


def test_selects_patch_skill_for_patch_questions():
    skills = load_relevant_skills("Show devices with failed patches and missing updates")
    names = {skill["name"] for skill in skills}
    assert "patch-management" in names


def test_skill_context_is_bounded_and_contains_only_selected_skills():
    context = render_skill_context("reboot a device service")
    assert "ninjaone-api-discovery" in context
    assert len(context) <= 16000
