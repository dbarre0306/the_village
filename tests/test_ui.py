import gradio as gr
import pytest

from the_village.discussion import DiscussionRunner
from the_village.state import DiscussionMessage, Death, GameState, Villager
from the_village.ui import (
    _living_ai_names,
    begin_discussion,
    format_alive_panel,
    format_deaths_panel,
    format_discussion_transcript,
    format_event_log,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
)


def make_state_with_one_death() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager", is_alive=False),
    ]
    return GameState(
        player_name="Dana",
        day_number=2,
        villagers=villagers,
        deaths=[Death(name="A", day_number=2)],
    )


def test_format_event_log_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_event_log(state) == "Nothing has happened yet."


def test_format_event_log_with_a_death():
    state = make_state_with_one_death()
    assert format_event_log(state) == "Monday morning: A was found dead."


def test_format_deaths_panel_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_deaths_panel(state) == "No one has been killed yet."


def test_format_deaths_panel_with_a_death():
    state = make_state_with_one_death()
    assert format_deaths_panel(state) == "- Monday: A"


def test_format_alive_panel_marks_player_and_excludes_dead_villagers():
    state = make_state_with_one_death()
    assert format_alive_panel(state) == "- Dana (me)"


def test_format_alive_panel_with_no_villagers():
    state = GameState(player_name="Dana", day_number=1)
    assert format_alive_panel(state) == "No one is left."


def test_start_game_rejects_blank_name():
    with pytest.raises(gr.Error):
        start_game("   ")


def test_start_game_returns_six_outputs_including_game_state():
    outputs = start_game("TestPlayer")

    assert len(outputs) == 6
    assert "TestPlayer" not in outputs[2]
    assert "TestPlayer" not in outputs[3]
    assert "TestPlayer (me)" in outputs[4]
    assert isinstance(outputs[5], GameState)
    assert outputs[5].player_name == "TestPlayer"


def test_format_discussion_transcript_with_no_messages():
    state = GameState(player_name="Dana")
    assert format_discussion_transcript(state) == "The discussion hasn't started yet."


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        player_name="Dana",
        discussion=[DiscussionMessage(day_number=1, speaker="A", message="hello")],
    )
    assert format_discussion_transcript(state) == "**A:** hello"


def test_living_ai_names_excludes_player_and_dead_villagers():
    state = GameState(
        player_name="Dana",
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
            Villager(name="D", player_type="villager", is_alive=False),
            Villager(name="E", player_type="werewolf"),
        ],
    )
    assert _living_ai_names(state) == ["A", "E"]


def test_begin_discussion_yields_waiting_status_when_only_player_active():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )

    events = list(begin_discussion(state))

    runner, transcript, dropdown_update, input_visibility, status_update = events[-1]
    assert isinstance(runner, DiscussionRunner)
    assert input_visibility["visible"] is True
    assert dropdown_update["choices"] == []


def test_send_discussion_turn_records_message_and_shows_waiting_status():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    runner = DiscussionRunner(state=state, agents={}, budgets={"Dana": 3}, queue=["Dana"])

    events = list(send_discussion_turn(runner, "I'm scared.", ""))

    _, transcript, _, input_visibility, _ = events[-1]
    assert "I'm scared." in transcript
    assert input_visibility["visible"] is True


def test_pass_discussion_turn_marks_player_passed_and_shows_ended_status():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    runner = DiscussionRunner(state=state, agents={}, budgets={"Dana": 3}, queue=["Dana"])

    events = list(pass_discussion_turn(runner))

    _, _, _, input_visibility, status_update = events[-1]
    assert "Dana" in runner.passed
    assert input_visibility["visible"] is False
    assert status_update["visible"] is True


def test_send_discussion_turn_wraps_unexpected_errors_as_gr_error():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state, agents={}, budgets={"Dana": 3, "A": 3}, queue=["A"]
    )

    with pytest.raises(gr.Error):
        list(send_discussion_turn(runner, "hello", ""))
