# tests/test_concurrency.py
import asyncio
from unittest.mock import patch

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.village_flow import VillageFlow

from conftest import _decline_and_abstain_akickoff


async def _run_one_session(player_name: str) -> str:
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": player_name}))

    while True:
        item = await bridge.outbox.get()
        if item == FlowStatus.VOTING_COMPLETE:
            break
        if item == FlowStatus.DISCUSSION_COMPLETE:
            bridge.resolve_input(PlayerInput())  # -> begin voting
        elif item == FlowStatus.WAITING_FOR_VOTE:
            bridge.resolve_input(PlayerInput(text=None))  # player abstains
        elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER) or not isinstance(
            item, FlowStatus
        ):
            bridge.resolve_input(PlayerInput(text=None))

    await flow_task
    return flow.state.user_player_name


async def test_two_village_flows_complete_independently_when_run_concurrently():
    with patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff):
        results = await asyncio.gather(
            _run_one_session("Alice"),
            _run_one_session("Bob"),
        )

    assert set(results) == {"Alice", "Bob"}
