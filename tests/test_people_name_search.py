from ninjaone_agent.agent import (
    _people_name_search_intent,
    _record_matches_person_name,
)


def test_people_name_search_extracts_explicit_name_filters():
    assert _people_name_search_intent(
        "How many users are in NinjaOne with name Rene?"
    ) == "Rene"
    assert _people_name_search_intent("Find technicians named Rene") == "Rene"
    assert _people_name_search_intent("Show contacts whose full name contains Rene") == "Rene"
    assert _people_name_search_intent("List all users") is None


def test_people_name_search_matches_common_name_fields_case_insensitively():
    assert _record_matches_person_name(
        {"firstName": "René", "lastName": "Dabhade"}, "rené"
    )
    assert _record_matches_person_name(
        {"displayName": "Rene Dabhade"}, "rene"
    )
    assert _record_matches_person_name(
        {"name": "Rene"}, "RENE"
    )
    assert not _record_matches_person_name(
        {"firstName": "Amit", "lastName": "Dabhade"}, "rene"
    )
