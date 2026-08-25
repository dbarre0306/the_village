# tests/test_flow.py
import asyncio
from unittest.mock import patch

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.village_flow import VillageFlow
from the_village.voting import VoteOutcome

from conftest import _decline_and_abstain_akickoff


async def test_village_flow_produces_valid_night_one_result_and_pauses_for_discussion():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    # announce_death() puts the victim's name (a bare str) on the outbox as
    # the very first item -- nothing else is queued before it, since
    # run_discussion() (which queues DiscussionMessage/FlowStatus items)
    # only starts after this pause is resolved.
    player_found_dead = await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state = flow.state
    assert len(state.players) == 7
    assert state.day_number == 1
    assert state.current_day.player_found_dead == player_found_dead

    killed = next(v for v in state.players if v.name == player_found_dead)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.players if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True


async def test_village_flow_builds_player_agents_onto_the_bridge():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    ai_names = {
        v.name for v in flow.state.players if v.player_type in ("villager", "werewolf")
    }
    assert set(bridge.player_agents.keys()) == ai_names


async def test_village_flow_reaches_voting_complete_with_an_outcome():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    with patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff):
        await bridge.outbox.get()  # death announcement
        bridge.resolve_input(PlayerInput())  # -> begin discussion

        outcome = None
        while True:
            item = await bridge.outbox.get()
            if item == FlowStatus.DISCUSSION_COMPLETE:
                bridge.resolve_input(PlayerInput())  # -> begin voting
            elif item == FlowStatus.WAITING_FOR_VOTE:
                bridge.resolve_input(PlayerInput(text=None))  # player abstains
            elif isinstance(item, VoteOutcome):
                outcome = item
            elif item == FlowStatus.VOTING_COMPLETE:
                break
            elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER):
                bridge.resolve_input(PlayerInput(text=None))

        await task

    assert outcome is not None
    assert outcome.day_number == 1
    assert outcome.tally == {}
    assert outcome.lynched is None
    assert flow.state.day_number == 2
