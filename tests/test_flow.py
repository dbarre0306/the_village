# tests/test_flow.py
import asyncio

from the_village.bridge import PlayerInput, SessionBridge
from the_village.village_flow import VillageFlow


async def test_village_flow_produces_valid_night_one_result_and_pauses_for_discussion():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": "Dana"}))

    # announce_death() puts the victim's name (a bare str) on the outbox as
    # the very first item -- nothing else is queued before it, since
    # run_discussion() (which queues DiscussionMessage/FlowStatus items)
    # only starts after this pause is resolved.
    player_killed = await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state = flow.state
    assert len(state.players) == 7
    assert state.day_number == 2
    assert state.current_day.player_killed == player_killed

    killed = next(v for v in state.players if v.name == player_killed)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.players if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True


async def test_village_flow_builds_player_agents_onto_the_bridge():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": "Dana"}))

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
