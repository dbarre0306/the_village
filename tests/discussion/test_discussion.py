import asyncio
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion.ai_speaker import _SpeakerOutput
from the_village.discussion.discussion import NUMBER_OF_ROUNDS, Discussion
from the_village.discussion.speaker import DECLINED_TO_RESPOND, _AddressResolution, _Speaker
from the_village.state import DiscussionMessage, GameState, Player


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="werewolf", is_pack_leader=True),
        Player(name="D", player_type="werewolf"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def _stub_agents(names: list[str]) -> dict[str, Agent]:
    return {name: _stub_agent() for name in names}


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def _decline_result():
    return _crew_result(_SpeakerOutput(has_something_to_say=False), None)


class NoShuffleRandom:
    """A `random.Random` stand-in whose `shuffle` is a no-op, so a round's
    order is exactly `names_of_living_players`' insertion order -- lets a
    test assert on *which* participant produces which scripted response
    without depending on a real shuffle's output for a given seed."""

    def shuffle(self, _seq):
        pass

    def randrange(self, start, _stop):
        return start


async def test_runs_two_rounds_where_everyone_gets_a_turn():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(text=None)

    with (
        patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        discussion = Discussion(
            state=make_discussion_state(),
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst_agent=_stub_agent(),
            rng=random.Random(1),
        )
        transcript = await discussion.run()

    # Everyone declines every turn in this test, so no DiscussionMessages are
    # recorded -- what's under test is that the discussion runs to
    # completion (doesn't hang) across two full rounds without error.
    assert transcript == []


async def test_resolves_a_bonus_reply_chain():
    bridge = SessionBridge()
    # make_discussion_state()'s villagers list is [Dana, A, B, C, D]; with no
    # shuffling, round order is exactly that -- Dana first (the player,
    # auto-passes below), then A, whose scripted response addresses B; B's
    # own scripted decline stops the chain there.
    state = make_discussion_state()

    speak_and_address_b = _crew_result(
        _SpeakerOutput(has_something_to_say=True, text="B, where were you?"),
        _AddressResolution(addressed_to="B"),
    )
    responses = iter([speak_and_address_b] + [_decline_result()] * 20)

    async def scripted_akickoff(*_args, **_kwargs):
        return next(responses)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(text=None)

    with (
        patch("crewai.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        discussion = Discussion(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst_agent=_stub_agent(),
            rng=NoShuffleRandom(),
        )
        transcript = await discussion.run()

    addressed_messages = [m for m in transcript if m.text == "B, where were you?"]
    assert len(addressed_messages) == 1
    assert addressed_messages[0].player_name == "A"
    assert addressed_messages[0].addressed_to == "B"
    decline_replies = [
        m for m in transcript if m.player_name == "B" and m.text == DECLINED_TO_RESPOND
    ]
    assert len(decline_replies) == 1


async def test_skips_a_player_already_used_in_the_reply_chain():
    """A player pulled into an address-chain reply must not also get their
    still-pending scheduled main turn later in the same round -- that would
    let one player speak twice in a row within a single round."""
    bridge = SessionBridge()
    # make_discussion_state()'s villagers list is [Dana, A, B, C, D]; with no
    # shuffling, round order is exactly that -- Dana first (auto-passes),
    # then A, who addresses B. B replies via the chain, and B is also next up
    # in the main rotation.
    state = make_discussion_state()

    a_speaks_and_addresses_b = _crew_result(
        _SpeakerOutput(has_something_to_say=True, text="Where were you, B?"),
        _AddressResolution(addressed_to="B"),
    )
    b_chain_reply = _crew_result(
        _SpeakerOutput(has_something_to_say=True, text="I was home."),
        _AddressResolution(addressed_to=None),
    )
    # Filler has something to say every time it's asked, so an erroneous
    # extra main turn for B would show up as a second recorded B message
    # instead of silently declining.
    filler = _crew_result(
        _SpeakerOutput(has_something_to_say=True, text="Nothing new."),
        _AddressResolution(addressed_to=None),
    )
    responses = iter([a_speaks_and_addresses_b, b_chain_reply] + [filler] * 20)

    async def scripted_akickoff(*_args, **_kwargs):
        return next(responses)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(text=None)

    with (
        patch("crewai.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
        patch("the_village.discussion.discussion.NUMBER_OF_ROUNDS", 1),
    ):
        discussion = Discussion(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst_agent=_stub_agent(),
            rng=NoShuffleRandom(),
        )
        transcript = await discussion.run()

    b_messages = [m for m in transcript if m.player_name == "B"]
    assert len(b_messages) == 1
    assert b_messages[0].text == "I was home."


async def test_pauses_for_player_and_resumes():
    bridge = SessionBridge()
    state = make_discussion_state()

    async def scripted_akickoff(*_args, **_kwargs):
        return _decline_result()

    with patch("crewai.Crew.akickoff", new=scripted_akickoff):
        discussion = Discussion(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst_agent=_stub_agent(),
            rng=random.Random(1),
        )
        task = asyncio.create_task(discussion.run())

        # Drain the outbox for the whole run, answering every player-turn
        # pause immediately. The discussion asks the player once per round
        # (two rounds total), so more than one pause is expected here --
        # race each outbox.get() against the discussion task itself so a
        # final completion with nothing left in the outbox doesn't leave
        # this loop blocked on a get() that will never resolve.
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
                bridge.resolve_input(PlayerInput(text=None))
        assert seen_waiting_for_turn

        transcript = await task

    assert isinstance(transcript, list)


def test_number_of_rounds_is_two():
    assert NUMBER_OF_ROUNDS == 2


async def test_user_is_never_first_to_speak_at_the_start_of_a_discussion():
    """Even when the shuffle would put the human player first, the human
    must not open a discussion cold before hearing from anyone else."""
    bridge = SessionBridge()
    state = make_discussion_state()

    speak_order: list[str] = []
    original_speak = _Speaker.speak

    async def recording_speak(self, addressed_by=None):
        speak_order.append(self._player_name)
        return await original_speak(self, addressed_by)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(text=None)

    with (
        patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
        patch.object(_Speaker, "speak", recording_speak),
        patch("the_village.discussion.discussion.NUMBER_OF_ROUNDS", 1),
    ):
        discussion = Discussion(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst_agent=_stub_agent(),
            rng=NoShuffleRandom(),
        )
        await discussion.run()

    assert speak_order[0] != "Dana"


def test_swap_never_lands_the_human_first_when_an_ai_alternative_exists():
    """When the last-speaker-repeat rule forces a swap, the replacement
    should prefer an AI player over the human -- otherwise the human can
    randomly end up first as a side effect of a swap meant to displace
    someone else."""
    players = [
        Player(name="A", player_type="villager"),
        Player(name="Dana", player_type="user"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="werewolf", is_pack_leader=True),
        Player(name="D", player_type="werewolf"),
    ]
    state = GameState(user_player_name="Dana", players=players)
    # "A" spoke last in a previous round, so "A" must not lead off this
    # round -- with no shuffling, "A" is first and "Dana" is the very next
    # candidate the naive swap would reach for.
    state.current_day.discussion.append(
        DiscussionMessage(player_name="A", text="I have a theory.")
    )

    discussion = Discussion(
        state=state,
        bridge=SessionBridge(),
        player_agents=_stub_agents(["A", "B", "C", "D"]),
        analyst_agent=_stub_agent(),
        rng=NoShuffleRandom(),
    )

    ordered = discussion._build_shuffled_living_players()

    assert ordered[0] != "Dana"
