import asyncio
from types import SimpleNamespace

import gradio as gr
import pytest

from the_village import ui
from the_village.bridge import FlowFailed, FlowStatus, PlayerInput, SessionBridge
from the_village.state import Day, DiscussionMessage, GameState, Player, VoteRecord
from the_village.ui import (
    begin_discussion,
    cast_player_abstain,
    cast_player_vote,
    format_alive_panel,
    format_deaths_panel,
    format_discussion_transcript,
    format_event_log,
    format_lynched_panel,
    format_vote_result,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
)
from the_village.voting import VoteChoice, VoteOutcome


def make_state_with_one_death() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    return GameState(
        player_name="Dana",
        players=players,
        days=[Day(day_number=2, player_killed="A")],
    )


def test_format_event_log_with_no_deaths():
    state = GameState(player_name="Dana")
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
    state = GameState(player_name="Dana")
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
    state = GameState(player_name="Dana")
    assert format_alive_panel(state) == '<div class="chip-list">No one is left.</div>'


async def test_start_game_rejects_blank_name():
    with pytest.raises(gr.Error):
        async for _ in start_game("   "):
            pass


async def test_start_game_yields_once_paused_at_the_death_gate():
    outputs = [update async for update in start_game("TestPlayer")]

    assert len(outputs) == 1
    bridge = outputs[0][7]
    assert isinstance(bridge, SessionBridge)
    assert bridge.pending_input is not None


async def test_begin_discussion_resolves_the_death_gate_and_streams_to_completion():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [
        update async for update in begin_discussion(bridge, GameState())
    ]

    assert await waiter == PlayerInput()
    assert len(outputs) == 2  # immediate "hide button" yield, then completion


async def test_begin_discussion_is_a_noop_when_already_resolved():
    bridge = SessionBridge()  # nothing pending -- simulates a double-click
    outputs = [update async for update in begin_discussion(bridge, GameState())]
    assert outputs == []


async def test_send_discussion_turn_is_a_noop_on_blank_message():
    bridge = SessionBridge()
    outputs = [update async for update in send_discussion_turn(bridge, GameState(), "   ")]
    assert outputs == [(gr.skip(),) * 7]


async def test_send_discussion_turn_resolves_pending_input():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    async for _ in send_discussion_turn(bridge, GameState(), "I didn't do it!"):
        pass

    assert await waiter == PlayerInput(message="I didn't do it!")


async def test_pass_discussion_turn_resolves_pending_input_with_none():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    async for _ in pass_discussion_turn(bridge, GameState()):
        pass

    assert await waiter == PlayerInput(message=None)


async def test_begin_discussion_raises_gr_error_on_flow_failed():
    # begin_discussion resolves the death-gate future before it starts
    # draining the outbox, so a pending wait must exist first -- this
    # exercises _stream_bridge's FlowFailed handling through its one
    # caller in this test module, rather than reaching into a private name.
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        async for _ in begin_discussion(bridge, GameState()):
            pass

    waiter.cancel()


def _discussion_state() -> GameState:
    return GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
    )


async def test_streamed_discussion_message_appears_in_rendered_transcript(monkeypatch):
    # Regression test for the discussion transcript never rendering: the
    # background DiscussionRunner appends messages to this same live
    # GameState (as it does in production -- see the append below), so
    # _stream_bridge must render off of `state.days` as items stream in for
    # the transcript to ever show anything before the whole discussion
    # finishes.
    #
    # Zero out the pacing delay rather than monkeypatching asyncio.sleep
    # itself -- asyncio.sleep is also what the test below uses to yield
    # control to the concurrently-running waiter task, and a stub that
    # doesn't actually suspend (unlike real sleep(0)) breaks that
    # cooperative handoff in ways that are very confusing to debug.
    monkeypatch.setattr(ui, "SPEAKER_THINKING_DELAY_SECONDS", 0)

    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    state = _discussion_state()
    message = DiscussionMessage(speaker="A", message="I saw something strange.")
    # DiscussionRunner._record_message appends to state.current_day.discussion
    # before putting the message on the outbox -- mirror that ordering here.
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    assert any("I saw something strange." in t for t in transcripts)
    assert len(state.current_day.discussion) == 1
    assert state.current_day.discussion[0].message == "I saw something strange."
    waiter.cancel()


async def test_ai_turn_shows_pending_placeholder_before_revealing_message(monkeypatch):
    sleep_calls = []
    real_sleep = asyncio.sleep

    async def spy_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)  # still a real checkpoint, just instant

    monkeypatch.setattr(ui.asyncio, "sleep", spy_sleep)

    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await real_sleep(0)

    state = _discussion_state()
    message = DiscussionMessage(speaker="A", message="hi there")
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    pending_index = next(i for i, t in enumerate(transcripts) if "typing-indicator" in t)
    assert "hi there" not in transcripts[pending_index]
    assert "A:</span>" in transcripts[pending_index]
    assert "hi there" in transcripts[pending_index + 1]
    assert sleep_calls == [ui.SPEAKER_THINKING_DELAY_SECONDS]
    waiter.cancel()


async def test_ai_turn_reveal_is_pinned_to_its_own_item_when_runner_races_ahead(monkeypatch):
    # Regression: DiscussionRunner appends directly to the shared, live
    # GameState and doesn't wait for the UI to consume each outbox item
    # before continuing (see discussion.py's _resolve_address_chain, which
    # can resolve a reply in well under SPEAKER_THINKING_DELAY_SECONDS).
    # So while the UI is mid-delay showing A's "typing" placeholder, the
    # runner can already have appended -- and enqueued -- B's message.
    # format_discussion_transcript(state) was reading *all* of state's
    # current messages when revealing A's turn, so B's message leaked into
    # A's reveal, and B's later "typing" placeholder then showed after B's
    # real message had already been visible for a render or two -- the
    # flicker reported as a speaker's name reappearing with dots after
    # their message was already shown.
    state = _discussion_state()
    players = state.players + [Player(name="B", player_type="villager")]
    state = state.model_copy(update={"players": players})
    msg_a = DiscussionMessage(speaker="A", message="first message")
    state.current_day.discussion.append(msg_a)

    bridge = SessionBridge()
    real_sleep = asyncio.sleep

    async def spy_sleep(seconds):
        msg_b = DiscussionMessage(speaker="B", message="second message")
        state.current_day.discussion.append(msg_b)
        await bridge.outbox.put(msg_b)
        await real_sleep(0)

    monkeypatch.setattr(ui.asyncio, "sleep", spy_sleep)

    waiter = asyncio.create_task(bridge.wait_for_input())
    await real_sleep(0)

    await bridge.outbox.put(msg_a)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    reveal_index = next(i for i, t in enumerate(transcripts) if "first message" in t)
    assert "second message" not in transcripts[reveal_index]
    waiter.cancel()


async def test_player_message_shows_immediately_without_placeholder_or_sleep(monkeypatch):
    sleep_calls = []
    real_sleep = asyncio.sleep

    async def spy_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(ui.asyncio, "sleep", spy_sleep)

    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await real_sleep(0)
    # The player's own message is pushed onto the outbox exactly the same
    # way an AI turn is (discussion.py's _run_round doesn't distinguish),
    # so this exercises the same _stream_bridge path with speaker ==
    # state.player_name.
    state = _discussion_state()
    message = DiscussionMessage(speaker="Dana", message="It wasn't me!")
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [
        update async for update in send_discussion_turn(bridge, state, "It wasn't me!")
    ]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    assert not any("typing-indicator" in t for t in transcripts)
    assert any("It wasn't me!" in t for t in transcripts)
    assert sleep_calls == []
    waiter.cancel()


def test_format_discussion_transcript_with_no_messages():
    state = GameState(player_name="Dana")
    assert format_discussion_transcript(state) == ""


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="hello")])],
    )
    transcript = format_discussion_transcript(state)
    assert "A:</span> hello" in transcript
    assert "hello" in transcript


def test_format_discussion_transcript_with_pending_speaker_hides_its_message():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="hello")])],
    )
    transcript = format_discussion_transcript(state, limit=0, pending_speaker="A")
    assert "hello" not in transcript
    assert "typing-indicator" in transcript
    assert "A:</span>" in transcript


def test_format_discussion_transcript_with_pending_speaker_keeps_prior_messages():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[
            Day(
                day_number=1,
                discussion=[
                    DiscussionMessage(speaker="A", message="first"),
                    DiscussionMessage(speaker="Dana", message="second"),
                ],
            )
        ],
    )
    transcript = format_discussion_transcript(state, limit=1, pending_speaker="Dana")
    assert "first" in transcript
    assert "second" not in transcript
    assert "typing-indicator" in transcript


def test_format_lynched_panel_with_no_lynchings():
    state = GameState(player_name="Dana")
    assert (
        format_lynched_panel(state)
        == '<div class="chip-list">No one has been lynched yet.</div>'
    )


def test_format_lynched_panel_with_a_lynching():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    state = GameState(
        player_name="Dana",
        players=players,
        days=[Day(day_number=2, player_lynched="A")],
    )
    assert (
        format_lynched_panel(state)
        == '<div class="chip-list"><span class="villager-chip dead" '
        'style="color: var(--speaker-1)">A</span></div>'
    )


def test_vote_candidate_names_excludes_player_and_dead():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(player_name="Dana", players=players)
    assert ui._vote_candidate_names(state) == ["A"]


def test_vote_button_updates_labels_living_candidates_and_hides_extra_slots():
    players = [Player(name="Dana", player_type="user")] + [
        Player(name=n, player_type="villager") for n in ["A", "B"]
    ]
    state = GameState(player_name="Dana", players=players)

    updates = ui._vote_button_updates(state)

    assert len(updates) == ui.MAX_VOTE_CANDIDATES
    assert updates[0].value == "A"
    assert updates[0].visible is True
    assert f"speaker-btn-{ui._speaker_color_index('A', state)}" in updates[0].elem_classes
    assert updates[1].value == "B"
    assert updates[1].visible is True
    assert f"speaker-btn-{ui._speaker_color_index('B', state)}" in updates[1].elem_classes
    assert updates[2].visible is False


def test_begin_voting_shows_vote_controls_and_hides_begin_button():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
    )

    outputs = ui.begin_voting(state)

    (
        begin_button_update,
        title_update,
        row_update,
        *candidate_updates,
        status_update,
        discussion_status_update,
    ) = outputs
    assert begin_button_update["visible"] is False
    assert title_update["value"] == "### Sunday's Voting"
    assert title_update["visible"] is True
    assert row_update["visible"] is True
    assert len(candidate_updates) == ui.MAX_VOTE_CANDIDATES
    assert candidate_updates[0].value == "A"
    assert candidate_updates[0].visible is True
    assert status_update["visible"] is False
    # discussion_status is left untouched so its "moderator ended the
    # discussion" message stays visible through the voting phase.
    assert "visible" not in discussion_status_update
    assert "value" not in discussion_status_update


def test_build_app_does_not_raise():
    ui.build_app()


class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


def test_colored_name_wraps_name_in_speaker_color_span():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
    )
    assert ui._colored_name("A", state) == '<span style="color: var(--speaker-1)">A</span>'


def test_format_vote_result_lists_breakdown_and_lynch_outcome():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    outcome = VoteOutcome(
        day_number=2,
        votes=[
            VoteRecord(voter="Dana", target="A"),
            VoteRecord(voter="B", target=None),
        ],
        tally={"A": 1},
        lynched="A",
    )
    state = GameState(player_name="Dana", players=players, days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    dana = ui._colored_name("Dana", state)
    a = ui._colored_name("A", state)
    b = ui._colored_name("B", state)
    assert f"{dana} voted for {a}." in result
    assert f"{b} abstained." in result
    assert f"{a}: 1" in result
    assert f"{a} was lynched by the village." in result
    # Dana and A land in different speaker slots, so their spans differ.
    assert dana != a


def test_format_vote_result_omits_tally_line_when_no_non_abstain_votes():
    outcome = VoteOutcome(
        day_number=2,
        votes=[VoteRecord(voter="Dana", target=None)],
        tally={},
        lynched=None,
    )
    state = GameState(player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert f"{ui._colored_name('Dana', state)} abstained." in result
    # The tally separator only ever appears in the tally-counts line, so its
    # absence confirms no (empty) tally line was rendered.
    assert "  ·  " not in result


def test_format_vote_result_reports_tie():
    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1, "B": 1}, lynched=None)
    state = GameState(player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert "tied" in result.lower()


def test_format_vote_result_reports_no_votes():
    outcome = VoteOutcome(day_number=2, votes=[], tally={}, lynched=None)
    state = GameState(player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert "no one voted" in result.lower()


def test_cast_player_vote_hides_controls_before_blocking_call():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    events = cast_player_vote(state, bridge, "A")
    first_event = next(events)

    row_update, status_update, _, _ = first_event
    assert row_update["visible"] is False
    assert status_update["value"] == "Tallying the votes…"


def test_cast_player_vote_reveals_outcome_and_updates_panels():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    events = list(cast_player_vote(state, bridge, "A"))
    _, status_update, alive_panel_value, lynched_panel_value = events[-1]

    assert f"{ui._colored_name('A', state)} was lynched by the village." in status_update["value"]
    assert "Dana" in alive_panel_value
    # Target the actual chip markup for "A" rather than a bare substring
    # check -- "A" alone would also match unrelated text/markup.
    assert '>A</span>' not in alive_panel_value
    assert '>A</span>' in lynched_panel_value


def test_cast_player_vote_wraps_unexpected_errors_as_gr_error():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )

    class BoomAgent:
        def kickoff(self, *args, **kwargs):
            raise RuntimeError("boom")

    bridge = SessionBridge(player_agents={"A": BoomAgent()})

    events = cast_player_vote(state, bridge, "A")
    next(events)  # first yield: hides the ballot before the blocking call
    # The error path must restore the ballot so the player can actually
    # retry -- leaving it hidden after the error would strand them with no
    # way to vote again.
    row_update, status_update, _, _ = next(events)
    assert row_update["visible"] is True
    assert status_update["visible"] is True

    with pytest.raises(gr.Error):
        next(events)


def test_cast_player_abstain_records_no_target():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    list(cast_player_abstain(state, bridge))

    dana_record = next(v for v in state.current_day.votes if v.voter == "Dana")
    assert dana_record.target is None
