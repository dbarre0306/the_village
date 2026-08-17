from types import SimpleNamespace

import gradio as gr
import pytest

from the_village import ui
from the_village.discussion import DiscussionRunner, TurnOutput
from the_village.state import DiscussionMessage, Death, GameState, Villager
from the_village.ui import (
    begin_discussion,
    format_alive_panel,
    format_deaths_panel,
    format_discussion_transcript,
    format_event_log,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
)


class ScriptedAgent:
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=self._outputs.pop(0))


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
    assert format_event_log(state) == "<strong>Nothing has happened yet.</strong>"


def test_format_event_log_with_a_death():
    state = make_state_with_one_death()
    log = format_event_log(state)
    assert (
        log == "<strong>Monday morning: "
        f'<span class="{ui.DEATH_LINE_CLASS}">'
        "A was found dead, torn apart by a werewolf attack.</span></strong>"
    )


def test_format_deaths_panel_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_deaths_panel(state) == '<div class="chip-list">No one has been killed yet.</div>'


def test_format_deaths_panel_with_a_death():
    state = make_state_with_one_death()
    assert (
        format_deaths_panel(state)
        == '<div class="chip-list"><span class="villager-chip dead" '
        'style="color: var(--speaker-1)">A</span></div>'
    )


def test_format_alive_panel_marks_player_and_excludes_dead_villagers():
    state = make_state_with_one_death()
    assert (
        format_alive_panel(state)
        == '<div class="chip-list"><span class="villager-chip" '
        'style="color: var(--speaker-0)">Dana (me)</span></div>'
    )


def test_format_alive_panel_with_no_villagers():
    state = GameState(player_name="Dana", day_number=1)
    assert format_alive_panel(state) == '<div class="chip-list">No one is left.</div>'


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
    assert format_discussion_transcript(state) == ""


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        player_name="Dana",
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
        discussion=[DiscussionMessage(day_number=1, speaker="A", message="hello")],
    )
    transcript = format_discussion_transcript(state)
    assert "A:</span> hello" in transcript
    assert "hello" in transcript


def test_begin_discussion_yields_waiting_status_when_only_player_active():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )

    events = list(begin_discussion(state))

    (
        runner,
        transcript,
        textbox_update,
        input_visibility,
        status_update,
        begin_button_update,
        title_update,
    ) = events[-1]
    assert isinstance(runner, DiscussionRunner)
    assert input_visibility["visible"] is True


def test_send_discussion_turn_records_message_and_ends_discussion_when_player_is_sole_participant():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    runner = DiscussionRunner(state=state, agents={}, budgets={"Dana": 3}, queue=["Dana"])

    events = list(send_discussion_turn(runner, "I'm scared."))

    _, transcript, _, input_visibility, status_update, _, _ = events[-1]
    assert "I'm scared." in transcript
    # No one else is in the discussion to respond, so it ends rather than
    # asking Dana to repeat herself to an empty room.
    assert input_visibility["visible"] is False
    assert status_update["visible"] is True


def test_pass_discussion_turn_marks_player_passed_and_shows_ended_status():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    # Budget of 1 so this single pass exhausts it -- passing only costs one
    # budget point, so a player with budget remaining is not marked passed.
    runner = DiscussionRunner(state=state, agents={}, budgets={"Dana": 1}, queue=["Dana"])

    events = list(pass_discussion_turn(runner))

    _, _, _, input_visibility, status_update, _, _ = events[-1]
    assert "Dana" in runner.passed
    assert input_visibility["visible"] is False
    assert status_update["visible"] is True


def test_pass_discussion_turn_hides_input_row_before_asking_next_agent():
    call_order = []

    class RecordingAgent:
        def kickoff(self, messages, response_format=None):
            call_order.append("agent_called")
            return SimpleNamespace(
                pydantic=TurnOutput(has_something_to_say=True, message="hi there")
            )

    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state,
        agents={"A": RecordingAgent()},
        budgets={"Dana": 3, "A": 3},
        queue=["Dana", "A"],
    )

    events = pass_discussion_turn(runner)
    first_event = next(events)
    call_order.append("first_event_received")

    _, _, _, input_visibility, _, _, _ = first_event
    assert input_visibility["visible"] is False
    # The row must hide before the next villager's (potentially slow) turn
    # is generated, not only once that turn's message comes back.
    assert call_order == ["first_event_received"]


def test_format_discussion_transcript_with_pending_speaker_hides_its_message():
    state = GameState(
        player_name="Dana",
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
        discussion=[DiscussionMessage(day_number=1, speaker="A", message="hello")],
    )
    transcript = format_discussion_transcript(state, pending_speaker="A")
    assert "hello" not in transcript
    assert "typing-indicator" in transcript
    assert "A:</span>" in transcript


def test_format_discussion_transcript_with_pending_speaker_keeps_prior_messages():
    state = GameState(
        player_name="Dana",
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
        discussion=[
            DiscussionMessage(day_number=1, speaker="A", message="first"),
            DiscussionMessage(day_number=1, speaker="Dana", message="second"),
        ],
    )
    transcript = format_discussion_transcript(state, pending_speaker="Dana")
    assert "first" in transcript
    assert "second" not in transcript
    assert "typing-indicator" in transcript


def test_ai_turn_shows_pending_spinner_before_revealing_message(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(ui.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state,
        agents={
            "A": ScriptedAgent(
                [TurnOutput(has_something_to_say=True, message="hi there")]
            )
        },
        budgets={"Dana": 3, "A": 3},
        queue=["A"],
    )

    events = list(pass_discussion_turn(runner))
    transcripts = [event[1] for event in events]

    pending_index = next(i for i, t in enumerate(transcripts) if "typing-indicator" in t)
    assert "hi there" not in transcripts[pending_index]
    assert "A:</span>" in transcripts[pending_index]
    assert "hi there" in transcripts[pending_index + 1]
    assert sleep_calls == [ui.SPEAKER_THINKING_DELAY_SECONDS]


def test_player_message_shows_immediately_without_spinner_or_sleep(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(ui.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    runner = DiscussionRunner(
        state=state, agents={}, budgets={"Dana": 3}, queue=["Dana"]
    )

    events = list(send_discussion_turn(runner, "I'm scared."))
    transcripts = [event[1] for event in events]

    # events[0] is the immediate row-hiding yield (a transcript no-op) that
    # fires before the addressed-to inference call; the player's message
    # itself shows on the very next yield, with no spinner or sleep.
    assert "typing-indicator" not in transcripts[1]
    assert "I'm scared." in transcripts[1]
    assert sleep_calls == []


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
        list(send_discussion_turn(runner, "hello"))
