#!/usr/bin/env python
import asyncio
import logging

from crewai.flow import Flow, listen, start

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion import DiscussionFlow
from the_village.night import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState

logger = logging.getLogger(__name__)


class VillageFlow(Flow[GameState]):
    def __init__(self, bridge: SessionBridge):
        super().__init__()
        self.bridge = bridge

    @start()
    async def setup_game(self):
        roster_state = build_initial_roster(self.state.player_name)
        self.state.day_number = roster_state.day_number
        self.state.villagers = roster_state.villagers

    @listen(setup_game)
    async def run_night_one(self):
        resolve_night_one(self.state)

    @listen(run_night_one)
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.deaths[-1])
        await self.bridge.wait_for_input()

    @listen(announce_death)
    async def run_discussion(self):
        logger.debug("VillageFlow.run_discussion: flow=%s bridge=%s entering", id(self), id(self.bridge))
        transcript = await DiscussionFlow(bridge=self.bridge).kickoff_async(
            inputs=self.state.model_dump()
        )
        self.state.discussion = transcript
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s discussion flow returned, transcript len=%s",
            id(self),
            id(self.bridge),
            len(transcript),
        )
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)


async def _auto_play_consumer(bridge: SessionBridge) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines so the
    flow reaches completion as a smoke test rather than hanging forever.
    """
    while True:
        item = await bridge.outbox.get()
        print(item)
        if item == FlowStatus.DISCUSSION_COMPLETE:
            return
        if item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER) or (
            not isinstance(item, FlowStatus)
        ):
            bridge.resolve_input(PlayerInput(message=None))


async def _kickoff_async():
    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(village_flow.kickoff_async(inputs={"player_name": "TestPlayer"}))
    consumer_task = asyncio.create_task(_auto_play_consumer(bridge))
    await flow_task
    consumer_task.cancel()
    print(village_flow.state.model_dump_json(indent=2))


def kickoff():
    asyncio.run(_kickoff_async())


def plot():
    village_flow = VillageFlow(bridge=SessionBridge())
    village_flow.plot()


if __name__ == "__main__":
    kickoff()
