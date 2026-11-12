from ninjaone_agent.agent import _likely_live_request
from ninjaone_agent.skills import load_relevant_skills


def test_fuzzy_machine_language_selects_device_skill():
    names = {item["name"] for item in load_relevant_skills("Which machines have stale check-ins?")}
    assert "devices-inventory" in names
    assert "devices-inventory" in {
        item["name"] for item in load_relevant_skills("Which computers have not checked in recently?")
    }


def test_fuzzy_customer_language_selects_organization_skill():
    names = {item["name"] for item in load_relevant_skills("Show all customers and sites")}
    assert "organizations-locations" in names


def test_live_request_detector_handles_fuzzy_rmm_questions():
    assert _likely_live_request("Which ones are offline and need attention?")
    assert _likely_live_request("What needs attention across the fleet?")
    assert _likely_live_request("Can you restart the NinjaOne service?")
    assert not _likely_live_request("Hello, how are you?")
