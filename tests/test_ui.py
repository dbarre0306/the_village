import asyncio

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
    format_completed_round_history,
    format_deaths_panel,
    format_discussion_transcript,
    format_latest_death_announcement,
    format_lynched_panel,
    format_vote_result,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
    start_voting,
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
        days=[Day(day_number=2, player_found_dead="A")],
    )


def test_format_latest_death_announcement_reports_only_the_most_recent_death():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(
        user_player_name="Dana",
        players=players,
        days=[
            Day(day_number=1, player_found_dead="A"),
            Day(day_number=2, player_found_dead="B"),
        ],
    )
    announcement = format_latest_death_announcement(state)
    assert "Tuesday morning" in announcement
    assert "B" in announcement
    assert "A" not in announcement


def test_format_deaths_panel_with_no_deaths():
    state = GameState(user_player_name="Dana")
    assert (
        format_deaths_panel(state)
        == '<div class="chip-list">No one has been killed yet.</div>'
    )


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

    outputs = [update async for update in begin_discussion(bridge, _discussion_state())]

    assert await waiter == PlayerInput()
    assert len(outputs) == 2  # immediate "hide button" yield, then completion


async def test_discussion_complete_status_value_differs_across_rounds():
    # Regression test: discussion_status.change() is what auto-triggers
    # start_voting (see build_app), and Gradio only fires .change() when a
    # component's value actually changes. The notice text is otherwise a
    # hardcoded constant, so a second round reusing the exact same value as
    # the first would never fire .change() -- silently hanging the flow
    # forever at run_discussion's post-discussion wait_for_input(), since
    # nothing ever resolves it. Each round's rendered value must differ.
    day_one_state = _discussion_state()
    bridge_one = SessionBridge()
    waiter_one = asyncio.create_task(bridge_one.wait_for_input())
    await asyncio.sleep(0)
    await bridge_one.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
    outputs_one = [
        update async for update in begin_discussion(bridge_one, day_one_state)
    ]
    await waiter_one
    status_one = outputs_one[-1][4]["value"]

    day_two_state = _discussion_state()
    day_two_state.days.append(Day(day_number=2, player_found_dead="A"))
    bridge_two = SessionBridge()
    waiter_two = asyncio.create_task(bridge_two.wait_for_input())
    await asyncio.sleep(0)
    await bridge_two.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
    outputs_two = [
        update async for update in begin_discussion(bridge_two, day_two_state)
    ]
    await waiter_two
    status_two = outputs_two[-1][4]["value"]

    assert status_one != status_two


def test_discussion_complete_notice_asks_the_ballot_question_when_human_is_alive():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user", is_alive=True),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1)],
    )
    assert (
        "The wolf is among you. Who do you think it is?"
        in ui._discussion_complete_notice(state)
    )


def test_discussion_complete_notice_omits_the_ballot_question_when_human_is_dead():
    # A dead human never gets a ballot -- Voting._build_voters only builds
    # voters for living players -- so asking them who they think the
    # werewolf is would be misleading.
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user", is_alive=False),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1)],
    )
    assert (
        "The wolf is among you. Who do you think it is?"
        not in ui._discussion_complete_notice(state)
    )


async def test_begin_discussion_shows_waiting_indicator_before_first_speaker():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, _discussion_state())]

    assert "typing-indicator" in outputs[0][1]
    waiter.cancel()


async def test_begin_discussion_clears_waiting_indicator_when_human_speaks_first():
    # human_speaker.py puts WAITING_FOR_TURN straight on the outbox with no
    # DiscussionMessage ahead of it when the human is first in the turn
    # order -- the anonymous "waiting" placeholder queued by begin_discussion's
    # own first yield must not survive into this state: the input row is
    # opening for the player to type, so showing a typing indicator (implying
    # someone else is about to speak) is stale and wrong.
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.WAITING_FOR_TURN)

    outputs = [update async for update in begin_discussion(bridge, _discussion_state())]

    transcript_value = ""
    for update in outputs:
        transcript_update = update[1]
        if isinstance(transcript_update, str):
            transcript_value = transcript_update
    assert "typing-indicator" not in transcript_value
    waiter.cancel()


async def test_begin_discussion_is_a_noop_when_already_resolved():
    bridge = SessionBridge()  # nothing pending -- simulates a double-click
    outputs = [update async for update in begin_discussion(bridge, GameState())]
    assert outputs == []


async def test_send_discussion_turn_is_a_noop_on_blank_message():
    bridge = SessionBridge()
    outputs = [
        update async for update in send_discussion_turn(bridge, GameState(), "   ")
    ]
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
        async for _ in begin_discussion(bridge, _discussion_state()):
            pass

    waiter.cancel()


def _discussion_state() -> GameState:
    return GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        # begin_discussion() always has a death to reveal by the time it's
        # callable in production -- announce_death sets this before the
        # Begin gate ever opens.
        days=[Day(day_number=1, player_found_dead="A")],
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
        i
        for i, t in enumerate(transcripts)
        if "typing-indicator" in t and "A:</span>" in t
    )
    assert "hi there" not in transcripts[pending_index]
    assert "hi there" in transcripts[pending_index + 1]
    assert sleep_calls == [ui.SPEAKER_THINKING_DELAY_SECONDS]
    waiter.cancel()


async def test_ai_turn_reveal_is_pinned_to_its_own_item_when_runner_races_ahead(
    monkeypatch,
):
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


async def test_player_message_shows_immediately_without_placeholder_or_sleep(
    monkeypatch,
):
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


def test_format_discussion_transcript_only_covers_the_current_day():
    # Regression test: format_discussion_transcript previously concatenated
    # every day's messages, so a new round's live transcript widget kept
    # growing with prior rounds' content instead of showing just the round
    # in progress (whose history now lives in format_completed_round_history
    # instead).
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[
            Day(
                day_number=1,
                discussion=[DiscussionMessage(player_name="A", text="day one")],
            ),
            Day(
                day_number=2,
                discussion=[DiscussionMessage(player_name="A", text="day two")],
            ),
        ],
    )
    transcript = format_discussion_transcript(state)
    assert "day two" in transcript
    assert "day one" not in transcript


def test_format_completed_round_history_with_no_completed_rounds():
    state = GameState(user_player_name="Dana", days=[Day(day_number=1)])
    assert format_completed_round_history(state) == ""


def test_format_completed_round_history_includes_transcript_and_vote_result():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    state = GameState(
        user_player_name="Dana",
        players=players,
        days=[
            Day(
                day_number=1,
                discussion=[DiscussionMessage(player_name="A", text="I'm innocent!")],
                votes=[VoteRecord(voter_name="Dana", target_name="A")],
                player_lynched="A",
            ),
            Day(day_number=2),  # the live, in-progress round -- excluded
        ],
    )
    history = format_completed_round_history(state)
    assert "### Monday" in history
    assert "I'm innocent!" in history
    assert "The moderator has stopped the discussion." in history
    assert "The Village Votes" in history
    assert "was lynched by the village." in history
    # The moderator's notice is never dropped once the round is archived --
    # it sits right before the vote recap heading, which supplies its own
    # divider line via CSS (no separate "---" needed here).
    notice_index = history.index("The moderator has stopped the discussion.")
    votes_index = history.index("The Village Votes", notice_index)
    assert notice_index < votes_index
    # This day has no player_found_dead set (only a lynching), so there's
    # no night-strip to render for it.
    assert ui.NIGHT_STRIP_CLASS not in history


def test_format_completed_round_history_interleaves_night_strip_between_days():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(
        user_player_name="Dana",
        players=players,
        days=[
            Day(day_number=1, player_found_dead="A"),
            Day(day_number=2, player_found_dead="B"),
            Day(day_number=3),  # the live, in-progress round -- excluded
        ],
    )
    history = format_completed_round_history(state)
    # Day two's chapter header is the main label, with its leading death
    # shown as a night-strip transition right after it (within Tuesday's
    # own panel -- Monday's panel, earlier in the doc, has its own night
    # strip for A too).
    tuesday_index = history.index("### Tuesday")
    night_strip_index = history.index(ui.NIGHT_STRIP_CLASS, tuesday_index)
    assert tuesday_index < night_strip_index
    assert "B" in history[tuesday_index:]


def test_format_completed_round_history_renders_oldest_day_first():
    # Each finished day gets its own panel, appended in chronological order
    # (Monday, Tuesday, ...) -- newly completed days land at the bottom of
    # the stack rather than immediately below the live card.
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(
        user_player_name="Dana",
        players=players,
        days=[
            Day(day_number=1, player_found_dead="A"),
            Day(day_number=2, player_found_dead="B"),
            Day(day_number=3),  # the live, in-progress round -- excluded
        ],
    )
    history = format_completed_round_history(state)
    assert history.index("### Monday") < history.index("### Tuesday")


def test_format_completed_round_history_wraps_each_day_in_its_own_panel():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(
        user_player_name="Dana",
        players=players,
        days=[
            Day(day_number=1, player_found_dead="A"),
            Day(day_number=2, player_found_dead="B"),
            Day(day_number=3),  # the live, in-progress round -- excluded
        ],
    )
    history = format_completed_round_history(state)
    assert history.count(f'<div class="{ui.DAY_PANEL_CLASS}">') == 2
    assert history.count("</div>") == 2
    first_panel_open = history.index(f'<div class="{ui.DAY_PANEL_CLASS}">')
    first_panel_close = history.index("</div>")
    assert first_panel_open < history.index("### Monday") < first_panel_close
    assert first_panel_close < history.index("### Tuesday")


def test_format_completed_round_history_includes_day_ones_own_death():
    # Regression: day one's death used to be excluded here as a special
    # case -- now the live panel reveals every day's death uniformly
    # (including day one's) behind its own Begin click, so the archive
    # includes it too once that day is folded in.
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    state = GameState(
        user_player_name="Dana",
        players=players,
        days=[
            Day(day_number=1, player_found_dead="A"),
            Day(day_number=2),  # the live, in-progress round -- excluded
        ],
    )
    history = format_completed_round_history(state)
    assert ui.NIGHT_STRIP_CLASS in history
    assert "A" in history


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[
            Day(
                day_number=1,
                discussion=[DiscussionMessage(player_name="A", text="hello")],
            )
        ],
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
        days=[
            Day(
                day_number=1,
                discussion=[DiscussionMessage(player_name="A", text="hello")],
            )
        ],
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
        days=[
            Day(
                day_number=1,
                discussion=[DiscussionMessage(player_name="A", text="hello")],
            )
        ],
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
    assert (
        f"speaker-btn-{ui._speaker_color_index('A', state)}" in updates[0].elem_classes
    )
    assert updates[1].value == "B"
    assert updates[1].visible is True
    assert (
        f"speaker-btn-{ui._speaker_color_index('B', state)}" in updates[1].elem_classes
    )
    assert updates[2].visible is False


async def test_start_voting_resolves_the_discussion_gate_and_reveals_the_ballot():
    # start_voting is auto-triggered by discussion_status.change now that
    # there's no "Begin Voting" button, so its only yield is the ballot
    # reveal -- there's no button to hide first.
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

    outputs = [update async for update in start_voting(bridge, state)]

    assert await waiter == PlayerInput()
    assert len(outputs) == 1
    (
        _bridge,
        row_update,
        *candidate_updates,
        status_update,
        discussion_status_update,
        alive_panel_update,
        deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
    ) = outputs[0]
    assert row_update["visible"] is True
    assert len(candidate_updates) == ui.MAX_VOTE_CANDIDATES
    assert candidate_updates[0].value == "A"
    assert candidate_updates[0].visible is True
    assert status_update["visible"] is False
    # The ballot question stays put here -- the human is alive and about to
    # vote, so it's still relevant; only the human-is-dead VoteOutcome path
    # swaps it.
    assert discussion_status_update == gr.update()
    # The next-day panel setup only happens once voting completes (see
    # _next_day_setup) -- untouched here, mid-vote.
    assert alive_panel_update == gr.update()
    assert deaths_panel_update == gr.update()
    assert begin_button_update == gr.update()
    assert discussion_title_update == gr.update()
    assert history_log_update == gr.update()
    assert discussion_transcript_update == gr.update()
    assert panel_death_line_update == gr.update()


async def test_start_voting_advances_to_next_day_on_voting_complete_instead_of_hanging():
    # Happens once the human dies on an earlier night: no _HumanVoter ever
    # pauses here, so Voting.run() completes entirely from AI votes with no
    # WAITING_FOR_VOTE ever arriving. start_voting must not block forever on
    # the empty queue, and -- since cast_player_vote never runs in this path
    # -- it must advance into the next day's Begin-gated panel itself (see
    # _next_day_setup) or the round has no way forward.
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
    await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
    # _next_day_setup drains the next night's death off the outbox, the same
    # shape VillageFlow.run_next_night puts there before pausing again.
    await bridge.outbox.put("A")

    outputs = [update async for update in start_voting(bridge, state)]

    assert await waiter == PlayerInput()
    # A no-op heartbeat yield fires first (see the comment in start_voting),
    # then the real Begin-gated panel once _next_day_setup's blocking wait
    # for the next night's death resolves.
    assert len(outputs) == 2
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        vote_status_update,
        discussion_status_update,
        alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        _history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
    ) = outputs[1]
    assert vote_status_update["visible"] is False
    assert discussion_status_update["visible"] is False
    # The Begin-gated panel for the new day: title shown, Begin button back,
    # death and discussion still hidden until that click. The button's label
    # must be resent (not just visible=True) -- Gradio fully unmounts a
    # hidden component (see discussion_input_row in _autofocus_js), so a
    # bare visible=True on remount would come back with no text at all.
    assert begin_button_update["visible"] is True
    assert begin_button_update["value"] == "Begin"
    assert discussion_title_update["visible"] is True
    assert "A" in alive_panel_update
    assert discussion_transcript_update["value"] == ""
    assert panel_death_line_update["visible"] is False


async def test_start_voting_renders_a_vote_outcome_before_voting_complete():
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
    outcome = VoteOutcome(day_number=1, votes=[], tally={}, lynched=None)
    await bridge.outbox.put(outcome)
    await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
    await bridge.outbox.put("A")

    outputs = [update async for update in start_voting(bridge, state)]

    assert await waiter == PlayerInput()
    # The outcome yield shows the result; VOTING_COMPLETE then yields a
    # no-op heartbeat (see the comment in start_voting) before finally
    # advancing into the next day's Begin-gated panel, since cast_player_vote
    # never runs in this path to do it.
    assert len(outputs) == 3
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        status_update,
        discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        _begin_button_update,
        _discussion_title_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
    ) = outputs[0]
    assert status_update["visible"] is True
    assert status_update["value"] == ui.format_vote_result(state, outcome)
    # discussion_status swaps its ballot question for the results label here
    # too -- cast_player_vote never runs in this human-is-dead path to do it.
    assert "The Village Votes" in discussion_status_update["value"]
    assert (
        "The wolf is among you. Who do you think it is?"
        not in discussion_status_update["value"]
    )
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        _status_update,
        _discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
    ) = outputs[2]
    assert begin_button_update["visible"] is True
    # The label must be resent, not just visible=True -- see the matching
    # assertion (and comment) in the cast_player_vote next-day test.
    assert begin_button_update["value"] == "Begin"
    assert discussion_title_update["visible"] is True


async def test_start_voting_is_a_noop_when_already_resolved():
    bridge = SessionBridge()  # nothing pending -- simulates a double-fire
    outputs = [update async for update in start_voting(bridge, GameState())]
    assert outputs == []


async def test_start_voting_second_invocation_does_not_resolve_a_later_pending_input():
    # discussion_status.change() fires again later in the same round when
    # start_voting's own VoteOutcome branch rewrites discussion_status to
    # "The Village Votes" label. That second, spurious invocation must not
    # resolve whatever pause point happens to be pending by then (e.g. the
    # next day's announce_death) -- doing so would silently skip that day's
    # Begin gate. See SessionBridge.voting_started.
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

    outputs = [update async for update in start_voting(bridge, state)]
    assert await waiter == PlayerInput()
    assert len(outputs) == 1
    assert bridge.voting_started is True

    # Simulate the next day's announce_death arming a new pause point, then
    # the spurious second start_voting() call discussion_status's rewrite
    # would trigger.
    next_waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    second_outputs = [update async for update in start_voting(bridge, state)]

    # Yields a single no-op tuple rather than nothing at all -- a
    # zero-yield generator invocation appears to leave Gradio's bound
    # outputs blank instead of untouched (see the comment in start_voting).
    assert len(second_outputs) == 1
    assert second_outputs[0] == (gr.update(),) * (11 + ui.MAX_VOTE_CANDIDATES)
    assert not next_waiter.done()
    next_waiter.cancel()


async def test_start_voting_raises_gr_error_on_flow_failed():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        async for _ in start_voting(bridge, GameState()):
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
    assert (
        ui._colored_name("A", state) == '<span style="color: var(--speaker-1)">A</span>'
    )


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
    state = GameState(
        user_player_name="Dana", players=players, days=[Day(day_number=2)]
    )

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
    (
        row_update,
        status_update,
        _alive_panel_update,
        _lynched_panel_update,
        _deaths_panel_update,
        _begin_button_update,
        _discussion_title_update,
        discussion_status_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
    ) = first_event
    assert row_update["visible"] is False
    assert status_update["value"] == "Tallying the votes…"
    # The ballot question is answered the moment the player picks -- it
    # shouldn't linger through tallying until the outcome arrives.
    assert (
        "The wolf is among you. Who do you think it is?"
        not in discussion_status_update["value"]
    )
    assert "The Village Votes" in discussion_status_update["value"]
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
    _row_update, status_update, alive_panel_value, lynched_panel_value, *_ = (
        await events.__anext__()
    )

    assert (
        f"{ui._colored_name('A', state)} was lynched by the village."
        in status_update["value"]
    )
    assert "Dana" in alive_panel_value
    assert ">A</span>" not in alive_panel_value
    assert ">A</span>" in lynched_panel_value
    await events.aclose()


async def test_cast_player_vote_replaces_the_ballot_question_with_a_results_label():
    # discussion_status keeps showing "The wolf is among you. Who do you think it is?"
    # (set when discussion ended, see _discussion_complete_notice) unless
    # cast_player_vote itself swaps it out once the outcome is known -- it's
    # not touched anywhere else in the voting flow.
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1}, lynched="A")

    async def feed_outcome():
        next(p for p in state.players if p.name == "A").is_alive = False
        state.current_day.player_lynched = "A"
        await bridge.outbox.put(outcome)
        await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

    events = cast_player_vote(bridge, state, "A")
    await events.__anext__()  # the "Tallying..." yield; also resolves waiter
    await waiter
    await feed_outcome()
    outcome_event = await events.__anext__()
    discussion_status_update = outcome_event[7]

    assert (
        "The wolf is among you. Who do you think it is?"
        not in discussion_status_update["value"]
    )
    assert "The Village Votes" in discussion_status_update["value"]
    assert (
        "The moderator has stopped the discussion." in discussion_status_update["value"]
    )
    await events.aclose()


async def test_cast_player_vote_advances_to_next_days_begin_gated_panel_on_voting_complete():
    # The round auto-advances into the next day's panel as soon as the
    # outcome is known, but in the same Begin-gated state day one starts in
    # -- the death reveal and discussion don't start until that click (see
    # _next_day_setup). VillageFlow.run_voting/run_next_night mutate this
    # same shared GameState and advance to a new day before putting the
    # next morning's death (a bare str) on the outbox -- mirror that here.
    state = _voting_state()
    state.days.append(Day(day_number=3, player_found_dead="A"))
    next(p for p in state.players if p.name == "A").is_alive = False
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    outcome = VoteOutcome(day_number=2, votes=[], tally={}, lynched=None)

    events = cast_player_vote(bridge, state, None)
    await events.__anext__()  # the "Tallying..." yield; also resolves waiter
    await waiter
    await bridge.outbox.put(outcome)
    await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
    await bridge.outbox.put("A")  # the next night's death, drained by _next_day_setup
    await events.__anext__()  # the VoteOutcome yield
    await events.__anext__()  # the no-op heartbeat yield (see cast_player_vote)

    (
        _row_update,
        status_update,
        alive_panel_value,
        _lynched_panel_value,
        deaths_panel_value,
        begin_discussion_update,
        discussion_title_update,
        discussion_status_update,
        _history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
    ) = await events.__anext__()

    assert ">A</span>" not in alive_panel_value
    assert "A" in deaths_panel_value
    # Every day is Begin-gated now, including this one -- the button comes
    # back instead of staying hidden. Its label must be resent too, not just
    # visible=True -- see the matching assertion (and comment) in
    # test_start_voting_advances_to_next_day_on_voting_complete_instead_of_hanging.
    assert begin_discussion_update["visible"] is True
    assert begin_discussion_update["value"] == "Begin"
    assert discussion_title_update["visible"] is True
    assert discussion_title_update["value"] == "### Wednesday"
    assert discussion_status_update["visible"] is False
    # The death reveal and discussion wait for the Begin click -- nothing
    # here starts them automatically anymore.
    assert panel_death_line_update["visible"] is False
    assert discussion_transcript_update["value"] == ""
    # The just-finished round's vote result moves into history_log, so the
    # live vote_status widget (reused by the next round) is hidden instead
    # of continuing to show it.
    assert status_update["visible"] is False

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()


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
