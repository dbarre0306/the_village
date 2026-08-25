import asyncio

import gradio as gr
import pytest

from the_village import ui
from the_village.bridge import FlowFailed, FlowStatus, PlayerInput, SessionBridge
from the_village.state import Day, DiscussionMessage, GameState, Player, VoteRecord
from the_village.ui import (
    begin_discussion,
    begin_voting,
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
from the_village.voting import VoteOutcome


def make_state_with_one_death() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    return GameState(
        user_player_name="Dana",
        players=players,
        days=[Day(day_number=2, player_killed="A")],
    )


def test_format_event_log_with_no_deaths():
    state = GameState(user_player_name="Dana")
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
    state = GameState(user_player_name="Dana")
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
    state = GameState(user_player_name="Dana")
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


async def test_begin_discussion_shows_waiting_indicator_before_first_speaker():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [
        update async for update in begin_discussion(bridge, GameState())
    ]

    assert "typing-indicator" in outputs[0][1]
    waiter.cancel()


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

    assert await waiter == PlayerInput(text="I didn't do it!")


async def test_pass_discussion_turn_resolves_pending_input_with_none():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    async for _ in pass_discussion_turn(bridge, GameState()):
        pass

    assert await waiter == PlayerInput(text=None)


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
        user_player_name="Dana",
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
    message = DiscussionMessage(player_name="A", text="I saw something strange.")
    # DiscussionRunner._record_message appends to state.current_day.discussion
    # before putting the message on the outbox -- mirror that ordering here.
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    assert any("I saw something strange." in t for t in transcripts)
    assert len(state.current_day.discussion) == 1
    assert state.current_day.discussion[0].text == "I saw something strange."
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
    message = DiscussionMessage(player_name="A", text="hi there")
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    pending_index = next(
        i for i, t in enumerate(transcripts) if "typing-indicator" in t and "A:</span>" in t
    )
    assert "hi there" not in transcripts[pending_index]
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
    msg_a = DiscussionMessage(player_name="A", text="first message")
    state.current_day.discussion.append(msg_a)

    bridge = SessionBridge()
    real_sleep = asyncio.sleep

    async def spy_sleep(seconds):
        msg_b = DiscussionMessage(player_name="B", text="second message")
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
    # state.user_player_name.
    state = _discussion_state()
    message = DiscussionMessage(player_name="Dana", text="It wasn't me!")
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
    state = GameState(user_player_name="Dana")
    assert format_discussion_transcript(state) == ""


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(player_name="A", text="hello")])],
    )
    transcript = format_discussion_transcript(state)
    assert "A:</span> hello" in transcript
    assert "hello" in transcript


def test_format_discussion_transcript_with_pending_speaker_hides_its_message():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(player_name="A", text="hello")])],
    )
    transcript = format_discussion_transcript(state, limit=0, pending_speaker="A")
    assert "hello" not in transcript
    assert "typing-indicator" in transcript
    assert "A:</span>" in transcript


def test_format_discussion_transcript_with_pending_speaker_keeps_prior_messages():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[
            Day(
                day_number=1,
                discussion=[
                    DiscussionMessage(player_name="A", text="first"),
                    DiscussionMessage(player_name="Dana", text="second"),
                ],
            )
        ],
    )
    transcript = format_discussion_transcript(state, limit=1, pending_speaker="Dana")
    assert "first" in transcript
    assert "second" not in transcript
    assert "typing-indicator" in transcript


def test_format_discussion_transcript_with_waiting_shows_generic_indicator():
    state = GameState(user_player_name="Dana")
    transcript = format_discussion_transcript(state, waiting=True)
    assert "typing-indicator" in transcript
    assert ":</span>" not in transcript  # no speaker name attached


def test_format_discussion_transcript_with_waiting_keeps_prior_messages():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(player_name="A", text="hello")])],
    )
    transcript = format_discussion_transcript(state, limit=1, waiting=True)
    assert "hello" in transcript
    assert "typing-indicator" in transcript


def test_format_lynched_panel_with_no_lynchings():
    state = GameState(user_player_name="Dana")
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
        user_player_name="Dana",
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
    state = GameState(user_player_name="Dana", players=players)
    assert ui._vote_candidate_names(state) == ["A"]


def test_vote_button_updates_labels_living_candidates_and_hides_extra_slots():
    players = [Player(name="Dana", player_type="user")] + [
        Player(name=n, player_type="villager") for n in ["A", "B"]
    ]
    state = GameState(user_player_name="Dana", players=players)

    updates = ui._vote_button_updates(state)

    assert len(updates) == ui.MAX_VOTE_CANDIDATES
    assert updates[0].value == "A"
    assert updates[0].visible is True
    assert f"speaker-btn-{ui._speaker_color_index('A', state)}" in updates[0].elem_classes
    assert updates[1].value == "B"
    assert updates[1].visible is True
    assert f"speaker-btn-{ui._speaker_color_index('B', state)}" in updates[1].elem_classes
    assert updates[2].visible is False


async def test_begin_voting_resolves_the_discussion_gate_and_reveals_the_ballot():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1)],
    )
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.WAITING_FOR_VOTE)

    outputs = [update async for update in begin_voting(bridge, state)]

    assert await waiter == PlayerInput()
    # The immediate first yield is what actually sets the begin-button and
    # title updates -- the second (final) yield leaves them as no-ops since
    # nothing about them changes once WAITING_FOR_VOTE arrives.
    _bridge0, begin_button_update, title_update, *_rest = outputs[0]
    assert begin_button_update["visible"] is False
    assert title_update["value"] == "### Sunday's Voting"
    assert title_update["visible"] is True
    (
        _bridge,
        _begin_button_update,
        _title_update,
        row_update,
        *candidate_updates,
        status_update,
    ) = outputs[-1]
    assert row_update["visible"] is True
    assert len(candidate_updates) == ui.MAX_VOTE_CANDIDATES
    assert candidate_updates[0].value == "A"
    assert candidate_updates[0].visible is True
    assert status_update["visible"] is False


async def test_begin_voting_is_a_noop_when_already_resolved():
    bridge = SessionBridge()  # nothing pending -- simulates a double-click
    outputs = [update async for update in begin_voting(bridge, GameState())]
    assert outputs == []


async def test_begin_voting_raises_gr_error_on_flow_failed():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        async for _ in begin_voting(bridge, GameState()):
            pass

    waiter.cancel()


def test_build_app_does_not_raise():
    ui.build_app()


def test_colored_name_wraps_name_in_speaker_color_span():
    state = GameState(
        user_player_name="Dana",
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
            VoteRecord(voter_name="Dana", target_name="A"),
            VoteRecord(voter_name="B", target_name=None),
        ],
        tally={"A": 1},
        lynched="A",
    )
    state = GameState(user_player_name="Dana", players=players, days=[Day(day_number=2)])

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
        votes=[VoteRecord(voter_name="Dana", target_name=None)],
        tally={},
        lynched=None,
    )
    state = GameState(user_player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert f"{ui._colored_name('Dana', state)} abstained." in result
    # The tally separator only ever appears in the tally-counts line, so its
    # absence confirms no (empty) tally line was rendered.
    assert "  ·  " not in result


def test_format_vote_result_reports_tie():
    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1, "B": 1}, lynched=None)
    state = GameState(user_player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert "tied" in result.lower()


def test_format_vote_result_reports_no_votes():
    outcome = VoteOutcome(day_number=2, votes=[], tally={}, lynched=None)
    state = GameState(user_player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert "no one voted" in result.lower()


def _voting_state() -> GameState:
    return GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )


async def test_cast_player_vote_resolves_the_ballot_and_hides_controls_before_the_reveal():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    events = cast_player_vote(bridge, state, "A")
    first_event = await events.__anext__()

    assert await waiter == PlayerInput(text="A")
    row_update, status_update, _, _ = first_event
    assert row_update["visible"] is False
    assert status_update["value"] == "Tallying the votes…"
    await events.aclose()


async def test_cast_player_vote_reveals_outcome_and_updates_panels():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1}, lynched="A")

    async def feed_outcome():
        # Voting.run() (invoked by VillageFlow.run_voting in the background
        # Flow task) mutates this same shared GameState -- marking the
        # lynched player dead and recording the day's outcome -- before the
        # VoteOutcome is put on the bridge; mirror that ordering here.
        next(p for p in state.players if p.name == "A").is_alive = False
        state.current_day.player_lynched = "A"
        await bridge.outbox.put(outcome)
        await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

    events = cast_player_vote(bridge, state, "A")
    await events.__anext__()  # the "Tallying..." yield; also resolves waiter
    await waiter
    await feed_outcome()
    _row_update, status_update, alive_panel_value, lynched_panel_value = (
        await events.__anext__()
    )

    assert f"{ui._colored_name('A', state)} was lynched by the village." in status_update["value"]
    assert "Dana" in alive_panel_value
    assert '>A</span>' not in alive_panel_value
    assert '>A</span>' in lynched_panel_value
    await events.aclose()


async def test_cast_player_vote_raises_gr_error_on_flow_failed():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    events = cast_player_vote(bridge, state, "A")
    await events.__anext__()
    await waiter
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        await events.__anext__()


async def test_cast_player_abstain_resolves_with_no_target():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    events = cast_player_abstain(bridge, state)
    await events.__anext__()

    assert await waiter == PlayerInput(text=None)
    await events.aclose()
