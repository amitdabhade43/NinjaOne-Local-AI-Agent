from ninjaone_agent.skills import load_relevant_skills


def test_operations_workflow_skill_is_selected_for_cross_resource_audits():
    skills = load_relevant_skills(
        "investigate fleet health audit root cause patch history compliance",
        max_skills=3,
    )
    assert "operations-workflows" in {skill["name"] for skill in skills}
