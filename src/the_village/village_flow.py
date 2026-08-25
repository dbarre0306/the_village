#!/usr/bin/env python
import asyncio
import logging

from crewai import Agent
from crewai.flow import Flow, listen, start

from the_village.agents import build_agent, build_conversation_analyst_agent
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion import Discussion
from the_village.night import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState
from the_village.voting import Voting

logger = logging.getLogger(__name__)


class VillageFlow(Flow[GameState]):
    def __init__(self, bridge: SessionBridge):
        super().__init__()
        self.bridge = bridge
        self._player_agents: dict[str, Agent] = {}
        self._analyst_agent: Agent | None = None

    @start()
    async def setup_game(self):
        roster_state = build_initial_roster(self.state.user_player_name)
        self.state.days = roster_state.days
        self.state.players = roster_state.players
        self._player_agents = self._build_ai_agents()
        self._analyst_agent = build_conversation_analyst_agent()
        self.bridge.player_agents = self._player_agents

    @listen(setup_game)
    async def run_night_one(self):
        resolve_night_one(self.state)

    @listen(run_night_one)
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.current_day.player_killed)
        await self.bridge.wait_for_input()

    @listen(announce_death)
    async def run_discussion(self):
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s entering",
            id(self),
            id(self.bridge),
        )
        await Discussion(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
            analyst_agent=self._analyst_agent,
        ).run()
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s discussion runner returned, transcript len=%s",
            id(self),
            id(self.bridge),
            len(self.state.current_day.discussion),
        )
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
        await self.bridge.wait_for_input()

    @listen(run_discussion)
    async def run_voting(self):
        logger.debug(
            "VillageFlow.run_voting: flow=%s bridge=%s entering",
            id(self),
            id(self.bridge),
        )
        outcome = await Voting(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
        ).run()
        logger.debug(
            "VillageFlow.run_voting: flow=%s bridge=%s voting runner returned, outcome=%s",
            id(self),
            id(self.bridge),
            outcome,
        )
        await self.bridge.outbox.put(outcome)
        await self.bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

    def _build_ai_agents(self):
        return {
            player.name: build_agent(player, self.state.players)
            for player in self.state.ai_players()
        }


async def _auto_play_consumer(bridge: SessionBridge) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines (and the
    player auto-abstains from voting) so the flow reaches completion as a
    smoke test rather than hanging forever.
    """
    while True:
        item = await bridge.outbox.get()
        print(item)
        if item == FlowStatus.VOTING_COMPLETE:
            return
        if item in (
            FlowStatus.WAITING_FOR_TURN,
            FlowStatus.WAITING_FOR_ANSWER,
            FlowStatus.WAITING_FOR_VOTE,
            FlowStatus.DISCUSSION_COMPLETE,
        ) or not isinstance(item, FlowStatus):
            bridge.resolve_input(PlayerInput(text=None))


async def _kickoff_async():
    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(
        village_flow.kickoff_async(inputs={"user_player_name": "TestPlayer"})
    )
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
