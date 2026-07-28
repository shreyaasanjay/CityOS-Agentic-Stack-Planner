"""Tests for TeLLMe explicit multi-agent routing fix.

Covers the bug where 'Agent A / Agent B' prompts with occupancy language were
misrouted as single_agent due to 'people' triggering occupancy_lookup intent.
"""

from __future__ import annotations

import pytest

from tellme_harness.query_analysis import analyze_query, _detect_explicit_multi_agent_coordination
from tellme_harness.route_policy import decide_route, score_query_analysis
from tellme_harness.schemas import TellMeQuery


def _make_query(text: str, space_id: str = "smart_room_1") -> TellMeQuery:
    import uuid, datetime
    return TellMeQuery(
        query_id=f"test_{uuid.uuid4().hex[:8]}",
        user_query=text,
        space_id=space_id,
        created_at=datetime.datetime.utcnow().isoformat(),
    )


# ---------------------------------------------------------------------------
# The primary regression: Agent A / Agent B prompt
# ---------------------------------------------------------------------------

AGENT_AB_QUERY = (
    "Agent A determines the number of people physically present in the conference room. "
    "Agent B verifies the occupancy count against the meeting attendance roster and "
    "produces a validated occupancy report."
)


def test_agent_ab_routes_multi_agent():
    q = _make_query(AGENT_AB_QUERY)
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent", (
        f"Expected multi_agent, got {decision.route}. "
        f"requires_explicit_multi_agent={analysis.requires_explicit_multi_agent}, "
        f"trigger_terms={analysis.trigger_terms_found}"
    )


def test_agent_ab_requires_tracefix():
    q = _make_query(AGENT_AB_QUERY)
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.requires_tracefix is True


def test_agent_ab_sets_requires_explicit_multi_agent():
    q = _make_query(AGENT_AB_QUERY)
    analysis = analyze_query(q)
    assert analysis.requires_explicit_multi_agent is True


def test_agent_ab_trigger_terms_populated():
    q = _make_query(AGENT_AB_QUERY)
    analysis = analyze_query(q)
    assert len(analysis.trigger_terms_found) > 0
    # Should detect both explicit agent name and coordination phrase
    assert "explicit_agent_name" in analysis.trigger_terms_found
    # "verify against" or "attendance roster" should be caught
    assert any(t in analysis.trigger_terms_found for t in (
        "verifies against", "verify against", "attendance roster", "meeting attendance"
    ))


def test_agent_ab_decision_includes_diagnostics():
    q = _make_query(AGENT_AB_QUERY)
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.explicit_agent_names_detected is True
    assert len(decision.trigger_terms_found) > 0


# ---------------------------------------------------------------------------
# Simple occupancy query still routes single_agent (no regression)
# ---------------------------------------------------------------------------

def test_simple_occupancy_still_single_agent():
    q = _make_query("How many people are in the conference room right now?")
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "single_agent"
    assert decision.requires_tracefix is False


# ---------------------------------------------------------------------------
# Quorum / audit / roster variants
# ---------------------------------------------------------------------------

def test_quorum_routes_multi_agent():
    q = _make_query(
        "Determine whether a quorum is present for the board meeting "
        "and verify the count against the registered attendees."
    )
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent"
    assert decision.requires_tracefix is True


def test_audit_report_routes_multi_agent():
    q = _make_query(
        "Agent A collects occupancy data. Agent B produces an audit report "
        "reconciling the physical count with the booking system."
    )
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent"


def test_resolve_discrepancies_routes_multi_agent():
    q = _make_query(
        "Two sensors disagree on the room count. "
        "Resolve discrepancies between the radar and the wifi estimates."
    )
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent"


# ---------------------------------------------------------------------------
# Detection unit tests
# ---------------------------------------------------------------------------

def test_detect_explicit_agent_name():
    detected, terms = _detect_explicit_multi_agent_coordination("agent a does X and agent b verifies")
    assert detected is True
    assert "explicit_agent_name" in terms


def test_detect_no_coordination_in_plain_query():
    detected, terms = _detect_explicit_multi_agent_coordination("how many people are in the room")
    assert detected is False
    assert terms == []


def test_detect_attendance_roster():
    detected, terms = _detect_explicit_multi_agent_coordination(
        "verify count against the meeting attendance roster"
    )
    assert detected is True
    assert "attendance roster" in terms or "meeting attendance" in terms


def test_detect_approve_or_reject():
    detected, terms = _detect_explicit_multi_agent_coordination(
        "the verifier should approve or reject the submitted count"
    )
    assert detected is True
    assert "approve or reject" in terms


# ---------------------------------------------------------------------------
# New coverage: semantic coordination phrases (expanded trigger list)
# ---------------------------------------------------------------------------

CONFERENCE_MEETING_QUERY = (
    "Determine whether a conference meeting may begin by confirming the number of attendees "
    "present and verifying that the attendance record matches the physical occupancy of the room."
)


def test_conference_meeting_routes_multi_agent():
    q = _make_query(CONFERENCE_MEETING_QUERY)
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent", (
        f"Expected multi_agent, got {decision.route}. "
        f"requires_explicit_multi_agent={analysis.requires_explicit_multi_agent}, "
        f"trigger_terms={analysis.trigger_terms_found}"
    )
    assert decision.requires_tracefix is True


def test_conference_meeting_trigger_terms_explain_decision():
    q = _make_query(CONFERENCE_MEETING_QUERY)
    analysis = analyze_query(q)
    assert analysis.requires_explicit_multi_agent is True
    terms = analysis.trigger_terms_found
    assert any("attendance" in t for t in terms), f"No attendance term in {terms}"
    assert any("verification" in t or "verif" in t for t in terms), f"No verification term in {terms}"


def test_confirm_attendance_compare_routes_multi_agent():
    q = _make_query(
        "Confirm attendance for the meeting by comparing the attendee list with physical occupancy."
    )
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent"
    assert decision.requires_tracefix is True


def test_verify_attendance_record_routes_multi_agent():
    q = _make_query(
        "Verify that the attendance record matches the number of people present."
    )
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert decision.route == "multi_agent"
    assert decision.requires_tracefix is True


def test_simple_occupancy_still_single_agent_expanded():
    """Expanded trigger list must not break plain occupancy queries."""
    for prompt in (
        "How many people are in the room right now?",
        "Is the conference room occupied?",
        "How many people attended the last meeting?",
    ):
        q = _make_query(prompt)
        analysis = analyze_query(q)
        decision = decide_route(q, analysis)
        assert decision.route == "single_agent", (
            f"Plain query wrongly routed to multi_agent: {prompt!r}\n"
            f"trigger_terms={analysis.trigger_terms_found}"
        )


def test_robot_corridor_coordination_routes_multi_agent():
    q = _make_query(
        "Create a CityOS coordination application for exactly three "
        "medication-delivery robot agents sharing one narrow hospital corridor. "
        "The robots coordinate corridor access among themselves using a verified "
        "mutual-exclusion protocol. Only one robot may occupy the corridor at a time. "
        "One robot has emergency priority, but ordinary robots must not starve. "
        "Each robot must request access, wait until access is granted by the protocol, "
        "cross the corridor, and release access so the next waiting robot may enter. "
        "Do not create a separate scheduler, controller, detector, pedestrian, or "
        "traffic-signal agent."
    )
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert analysis.requires_explicit_multi_agent is True
    assert any("coordination_participant" in term for term in analysis.trigger_terms_found)
    assert any("shared_resource_term" in term for term in analysis.trigger_terms_found)
    assert decision.route == "multi_agent"
    assert decision.requires_tracefix is True


def test_person_doing_on_date_routes_activity_lookup_single_agent():
    q = _make_query("What was the person doing in the room on June 24th?")
    analysis = analyze_query(q)
    decision = decide_route(q, analysis)
    assert analysis.intent == "activity_lookup"
    assert analysis.answer_type == "summary"
    assert "activities" in analysis.context_requirements
    assert decision.route == "single_agent"
    assert decision.selected_agent == "motion_context_agent"
    assert decision.requires_tracefix is False
