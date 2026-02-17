"""Tests for activity-type playbooks in the prompt system."""

from app.services.coach.prompts import build_system_prompt, ACTIVITY_PLAYBOOKS, PROMPT_VERSIONS


def test_build_prompt_includes_interval_playbook():
    prompt = build_system_prompt("coach_report_v1", "Intervals")
    assert "INTERVAL SESSION FOCUS" in prompt
    assert "rep_pace_consistency_cv" in prompt
    assert "workout_match" in prompt
    assert "HR drift" in prompt  # mentioned as "do not use"


def test_build_prompt_includes_long_run_playbook():
    prompt = build_system_prompt("coach_report_v1", "Long Run")
    assert "LONG RUN FOCUS" in prompt
    assert "durability" in prompt.lower()


def test_build_prompt_includes_easy_run_playbook():
    prompt = build_system_prompt("coach_report_v1", "Easy Run")
    assert "EASY RUN FOCUS" in prompt


def test_build_prompt_includes_tempo_playbook():
    prompt = build_system_prompt("coach_report_v1", "Tempo")
    assert "TEMPO RUN FOCUS" in prompt


def test_build_prompt_no_playbook_for_unknown_class():
    prompt = build_system_prompt("coach_report_v1", "Unknown Activity")
    # Should just be the base prompt, no playbook appended
    assert prompt == PROMPT_VERSIONS["coach_report_v1"]


def test_build_prompt_no_playbook_when_none():
    prompt = build_system_prompt("coach_report_v1", None)
    assert prompt == PROMPT_VERSIONS["coach_report_v1"]


def test_all_playbooks_are_non_empty():
    for activity_class, playbook in ACTIVITY_PLAYBOOKS.items():
        assert len(playbook.strip()) > 0, f"Playbook for {activity_class} is empty"
