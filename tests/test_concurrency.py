# tests/test_concurrency.py
import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion.discussion import AddressResolution, SpeakerOutput
from the_village.village_flow import VillageFlow


def _decline_result():
    return SimpleNamespace(
        tasks_output=[
            SimpleNamespace(pydantic=SpeakerOutput(has_something_to_say=False)),
            SimpleNamespace(pydantic=AddressResolution(addressed_to=None)),
        ]
    )


async def _run_one_session(player_name: str) -> str:
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": player_name}))

    while True:
        item = await bridge.outbox.get()
        if item == FlowStatus.DISCUSSION_COMPLETE:
            break
        bridge.resolve_input(PlayerInput(message=None))

    await flow_task
    return flow.state.user_player_name


async def test_two_village_flows_complete_independently_when_run_concurrently():
    with patch(
        "the_village.discussion.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())
    ):
        results = await asyncio.gather(
            _run_one_session("Alice"),
            _run_one_session("Bob"),
        )

    assert set(results) == {"Alice", "Bob"}
