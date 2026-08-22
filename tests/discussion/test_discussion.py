import asyncio
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.discussion.discussion import (
    DECLINED_TO_RESPOND,
    AddressResolution,
    DiscussionRunner,
    SpeakerOutput,
    _format_deaths,
    _format_history,
    _record_message,
    _resolve_target,
    _run_ai_turn,
    _run_player_turn,
)
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.state import Day, DiscussionMessage, GameState, Player


def test_format_deaths_with_no_deaths():
    state = GameState(player_name="Dana")
    assert _format_deaths(state) == "(No one has died yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(player_name="Dana", days=[Day(day_number=2, player_killed="D")])
    assert _format_deaths(state) == "D was found dead on Monday."


def test_format_history_with_no_messages():
    state = GameState(player_name="Dana")
    assert _format_history(state) == "(No discussion has happened yet.)"


def test_format_history_includes_prior_days_in_order():
    state = GameState(
        player_name="Dana",
        days=[
            Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="yesterday's message")]),
            Day(day_number=2, discussion=[DiscussionMessage(speaker="B", message="today's message")]),
        ],
    )
    assert (
        _format_history(state)
        == "A: yesterday's message\nB: today's message"
    )


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(player_name="Dana", players=players)


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def _stub_agents(names: list[str]) -> dict[str, Agent]:
    return {name: _stub_agent() for name in names}


def test_turn_output_has_no_addressed_to_field():
    assert "addressed_to" not in SpeakerOutput.model_fields


def test_record_message_appends_to_current_day_and_returns_it():
    state = make_discussion_state()
    msg = _record_message(state, "A", "hello", addressed_to="B")
    assert state.current_day.discussion == [msg]
    assert msg.speaker == "A"
    assert msg.message == "hello"
    assert msg.addressed_to == "B"
    assert state.current_day.day_number == 1


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_discussion_state()
    assert _resolve_target(None, state, exclude="A") is None
    assert _resolve_target("A", state, exclude="A") is None
    assert _resolve_target("Ghost", state, exclude="A") is None
    assert _resolve_target("B", state, exclude="A") == "B"


async def test_run_ai_turn_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(SpeakerOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message is None
    assert state.current_day.discussion == []


async def test_run_ai_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "B", "Where were you?", addressed_to="A")
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(SpeakerOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=asking
        )
    assert message.speaker == "A"
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_ai_turn_records_message_and_resolved_address():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                SpeakerOutput(has_something_to_say=True, message="I saw B leave."),
                AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message.message == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.current_day.discussion == [message]


async def test_run_ai_turn_discards_addressed_to_from_the_analyst_on_decline():
    """Even if the analyst task somehow returns an address for a decline (it
    still runs -- see the spec's resolved decision to keep one uniform
    two-task Crew shape), a decline's recorded message must not carry it."""
    state = make_discussion_state()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                SpeakerOutput(has_something_to_say=False), AddressResolution(addressed_to="B")
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message is None


async def test_run_player_turn_returns_none_on_scheduled_pass():
    # SessionBridge.resolve_input() is a no-op until wait_for_input() has a
    # pending future (see test_bridge.py), so the task must reach its await
    # point (a sleep(0) yield) before we resolve it -- calling resolve_input
    # first would hang forever.
    state = make_discussion_state()
    bridge = SessionBridge()
    task = asyncio.create_task(
        _run_player_turn(
            analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=None
        )
    )
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(message=None))
    message = await task
    assert message is None
    assert state.current_day.discussion == []


async def test_run_player_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "A", "Where were you?", addressed_to="Dana")
    bridge = SessionBridge()
    task = asyncio.create_task(
        _run_player_turn(
            analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=asking
        )
    )
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(message=None))
    message = await task
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_player_turn_resolves_address_via_the_analyst():
    state = make_discussion_state()
    bridge = SessionBridge()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(AddressResolution(addressed_to="B"))),
    ):
        task = asyncio.create_task(
            _run_player_turn(
                analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=None
            )
        )
        await asyncio.sleep(0)
        bridge.resolve_input(PlayerInput(message="B, where were you?"))
        message = await task
    assert message.speaker == "Dana"
    assert message.message == "B, where were you?"
    assert message.addressed_to == "B"


def make_discussion_runner_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="werewolf", is_pack_leader=True),
        Player(name="D", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", players=players)


def _decline_result():
    return _crew_result(SpeakerOutput(has_something_to_say=False), None)


async def test_discussion_runner_runs_two_rounds_where_everyone_gets_a_turn():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        runner = DiscussionRunner(
            state=make_discussion_runner_state(),
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=random.Random(1),
        )
        transcript = await runner.run()

    # Everyone declines every turn in this test, so no DiscussionMessages are
    # recorded -- what's under test is that the runner runs to completion
    # (doesn't hang) across two full rounds without error.
    assert transcript == []


class NoShuffleRandom:
    """A `random.Random` stand-in whose `shuffle` is a no-op, so a round's
    order is exactly `_living_participant_names`' insertion order -- lets a
    test assert on *which* participant produces which scripted response
    without depending on a real shuffle's output for a given seed."""

    def shuffle(self, _seq):
        pass

    def randrange(self, start, _stop):
        return start


async def test_discussion_runner_resolves_a_bonus_reply_chain():
    bridge = SessionBridge()
    # make_discussion_runner_state()'s villagers list is [Dana, A, B, C, D];
    # with no shuffling, round order is exactly that -- Dana first (the
    # player, auto-passes below), then A, whose scripted response addresses
    # B; B's own scripted decline stops the chain there.
    state = make_discussion_runner_state()

    speak_and_address_b = _crew_result(
        SpeakerOutput(has_something_to_say=True, message="B, where were you?"),
        AddressResolution(addressed_to="B"),
    )
    responses = iter([speak_and_address_b] + [_decline_result()] * 20)

    async def scripted_akickoff(*_args, **_kwargs):
        return next(responses)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.discussion.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        runner = DiscussionRunner(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=NoShuffleRandom(),
        )
        transcript = await runner.run()

    addressed_messages = [m for m in transcript if m.message == "B, where were you?"]
    assert len(addressed_messages) == 1
    assert addressed_messages[0].speaker == "A"
    assert addressed_messages[0].addressed_to == "B"
    decline_replies = [
        m for m in transcript if m.speaker == "B" and m.message == DECLINED_TO_RESPOND
    ]
    assert len(decline_replies) == 1


async def test_discussion_runner_skips_a_player_already_used_in_the_reply_chain():
    """A player pulled into an address-chain reply must not also get their
    still-pending scheduled main turn later in the same round -- that would
    let one player speak twice in a row within a single round."""
    bridge = SessionBridge()
    # make_discussion_runner_state()'s villagers list is [Dana, A, B, C, D];
    # with no shuffling, round order is exactly that -- Dana first (auto-
    # passes), then A, who addresses B. B replies via the chain, and B is
    # also next up in the main rotation.
    state = make_discussion_runner_state()

    a_speaks_and_addresses_b = _crew_result(
        SpeakerOutput(has_something_to_say=True, message="Where were you, B?"),
        AddressResolution(addressed_to="B"),
    )
    b_chain_reply = _crew_result(
        SpeakerOutput(has_something_to_say=True, message="I was home."),
        AddressResolution(addressed_to=None),
    )
    # Filler has something to say every time it's asked, so an erroneous
    # extra main turn for B would show up as a second recorded B message
    # instead of silently declining.
    filler = _crew_result(
        SpeakerOutput(has_something_to_say=True, message="Nothing new."),
        AddressResolution(addressed_to=None),
    )
    responses = iter([a_speaks_and_addresses_b, b_chain_reply] + [filler] * 20)

    async def scripted_akickoff(*_args, **_kwargs):
        return next(responses)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.discussion.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
        patch("the_village.discussion.discussion.NUMBER_OF_ROUNDS", 1),
    ):
        runner = DiscussionRunner(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=NoShuffleRandom(),
        )
        transcript = await runner.run()

    b_messages = [m for m in transcript if m.speaker == "B"]
    assert len(b_messages) == 1
    assert b_messages[0].message == "I was home."


async def test_discussion_runner_pauses_for_player_and_resumes():
    bridge = SessionBridge()
    state = make_discussion_runner_state()

    async def scripted_akickoff(*_args, **_kwargs):
        return _decline_result()

    with patch("the_village.discussion.discussion.Crew.akickoff", new=scripted_akickoff):
        runner = DiscussionRunner(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=random.Random(1),
        )
        task = asyncio.create_task(runner.run())

        # Drain the outbox for the whole run, answering every player-turn
        # pause immediately. The runner asks the player once per round (two
        # rounds total), so more than one pause is expected here -- race
        # each outbox.get() against the runner task itself so a final
        # completion with nothing left in the outbox doesn't leave this
        # loop blocked on a get() that will never resolve.
        seen_waiting_for_turn = False
        while not task.done():
            get_item = asyncio.ensure_future(bridge.outbox.get())
            done, _pending = await asyncio.wait(
                {task, get_item}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_item not in done:
                get_item.cancel()
                break
            item = get_item.result()
            if item == FlowStatus.WAITING_FOR_TURN:
                seen_waiting_for_turn = True
                bridge.resolve_input(PlayerInput(message=None))
        assert seen_waiting_for_turn

        transcript = await task

    assert isinstance(transcript, list)
