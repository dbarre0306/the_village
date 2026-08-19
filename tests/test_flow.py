# tests/test_flow.py
import asyncio

from the_village.bridge import PlayerInput, SessionBridge
from the_village.main import VillageFlow


async def test_village_flow_produces_valid_night_one_result_and_pauses_for_discussion():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": "Dana"}))

    # Drain until the death-announcement pause, then unblock it. We don't
    # drive a full discussion round here (that's DiscussionFlow's own test
    # suite in test_discussion.py) -- just confirm VillageFlow reaches and
    # respects the gate, then cancel rather than run a real discussion.
    from the_village.state import Death

    saw_death = False
    for _ in range(50):
        item = await bridge.outbox.get()
        if isinstance(item, Death):
            saw_death = True
            bridge.resolve_input(PlayerInput())
            break
    assert saw_death

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state = flow.state
    assert len(state.villagers) == 7
    assert len(state.deaths) == 1

    death = state.deaths[0]
    assert death.day_number == 2

    killed = next(v for v in state.villagers if v.name == death.name)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.villagers if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True
