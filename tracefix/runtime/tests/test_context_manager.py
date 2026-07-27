from tracefix.runtime.context_manager import (
    answer_uploaded_context_query,
    clear_context,
    get_context,
    set_context,
)


def teardown_function() -> None:
    clear_context()


def test_set_get_replace_and_clear_context() -> None:
    first = set_context({"people": [{"activity": "jumping"}]}, filename="one.json", size_bytes=12)
    assert first["loaded"] is True
    assert first["filename"] == "one.json"
    assert get_context()["data"]["people"][0]["activity"] == "jumping"

    second = set_context({"people_count": 3}, filename="two.json", size_bytes=14)
    assert second["filename"] == "two.json"
    assert get_context()["data"] == {"people_count": 3}

    clear_context()
    assert get_context() is None


def test_structured_answer_uses_uploaded_json_context() -> None:
    set_context(
        {
            "room": "conference",
            "people": [
                {"id": "p1", "activity": "jumping"},
                {"id": "p2", "activity": "sitting"},
                {"id": "p3", "activities": ["jumping", "waving"]},
            ],
        },
        filename="room_state.json",
        size_bytes=128,
    )

    answer = answer_uploaded_context_query("How many people are jumping?")

    assert answer is not None
    assert answer["status"] == "answered"
    assert answer["route_decision"]["selected_tool"] == "uploaded_json_context_lookup"
    assert answer["answer_summary"] == "2 jumping."
    assert answer["tracefix_task_spec"]["route"] == "single_agent"


def test_coordination_lane_ignores_uploaded_json_context() -> None:
    set_context({"people": [{"activity": "jumping"}]}, filename="room_state.json", size_bytes=64)

    answer = answer_uploaded_context_query(
        "Design a CityOS coordination protocol for three robots sharing a corridor."
    )

    assert answer is None
