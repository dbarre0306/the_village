#!/usr/bin/env python
import asyncio
import logging

from crewai import Agent
from crewai.flow import Flow, listen, or_, router, start

from the_village.agents import build_agent, build_conversation_analyst_agent
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion import Discussion
from the_village.pick_victim import WereWolfPack, kill_first_victim
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
        kill_first_victim(self.state)

    @listen(or_("run_night_one", "night_fell"))
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.current_day.player_found_dead)
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
        self.state.advance_day()
        await self.bridge.outbox.put(outcome)
        await self.bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

    @router(run_voting)
    async def run_next_night(self):
        await WereWolfPack(self.state, self._player_agents).kill_next_victim()
        # Routing back to a "night_fell" signal (rather than listening on
        # this method's own name) is what re-arms announce_death's or_()
        # each cycle -- crewai only rearms a fired or_() branch when a
        # @router emits a fresh signal, not on a plain @listen firing again.
        return "night_fell"

    def _build_ai_agents(self):
        return {
            player.name: build_agent(player, self.state.players)
            for player in self.state.ai_players()
        }


# VillageFlow's day/night cycle has no win condition yet and loops forever,
# so the CLI smoke test below has to stop itself after a few days rather
# than waiting for the flow to finish on its own.
_AUTO_PLAY_MAX_DAYS = 3


async def _auto_play_consumer(bridge: SessionBridge, max_days: int) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines (and the
    player auto-abstains from voting) so the flow reaches completion as a
    smoke test rather than hanging forever. Returns once `max_days` votes
    have completed; the caller cancels the (otherwise endless) flow task.
    """
    days_voted = 0
    while True:
        item = await bridge.outbox.get()
        print(item)
        if item == FlowStatus.VOTING_COMPLETE:
            days_voted += 1
            if days_voted >= max_days:
                return
            continue
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
    consumer_task = asyncio.create_task(_auto_play_consumer(bridge, _AUTO_PLAY_MAX_DAYS))
    await consumer_task
    flow_task.cancel()
    try:
        await flow_task
    except asyncio.CancelledError:
        pass
    print(village_flow.state.model_dump_json(indent=2))


def kickoff():
    asyncio.run(_kickoff_async())


def plot():
    village_flow = VillageFlow(bridge=SessionBridge())
    village_flow.plot()


if __name__ == "__main__":
    kickoff()
