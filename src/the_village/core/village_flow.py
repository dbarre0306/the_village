#!/usr/bin/env python
import asyncio
import logging

from crewai import Agent
from crewai.flow import Flow, listen, router, start

from the_village.agents import build_agent, build_conversation_analyst_agent
from the_village.core.bridge import FlowStatus, GameOverResult, PlayerInput, SessionBridge
from the_village.discussion import Discussion
from the_village.pick_victim import WereWolfPack, kill_first_victim
from the_village.roster import build_initial_roster
from the_village.core.state import GameState
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
        self.state.players = build_initial_roster(self.state.user_player_name)
        for player in self.state.players:
            print(f"[roster] {player.name}: {player.player_type}")
        self._player_agents = self._build_ai_agents()
        self._analyst_agent = build_conversation_analyst_agent()
        self.bridge.player_agents = self._player_agents

    @router(setup_game)
    async def run_night_one(self):
        kill_first_victim(self.state)
        # Emitting "night_fell" (rather than listening on this method's own
        # name) lets announce_death listen on a single signal shared with
        # run_next_night's router below.
        return "night_fell"

    @listen("night_fell")
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.current_day.player_found_dead)
        await self.bridge.wait_for_input()

    @router(announce_death)
    async def check_winner_after_kill(self):
        self.state.winner = self.state.determine_winner()
        return "game_over" if self.state.winner else "discussion"

    @listen("discussion")
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

    @router(run_voting)
    async def check_winner_after_lynching(self):
        self.state.winner = self.state.determine_winner()
        if self.state.winner:
            return "game_over"
        self.state.advance_day()
        return "continue_night"

    @router("continue_night")
    async def run_next_night(self):
        await WereWolfPack(self.state, self._player_agents).kill_next_victim()
        return "night_fell"

    @listen("game_over")
    async def finish_game(self):
        await self.bridge.outbox.put(
            GameOverResult(
                winner=self.state.winner,
                werewolf_names=self.state.werewolf_names(),
            )
        )

    def _build_ai_agents(self):
        return {
            player.name: build_agent(player, self.state.players)
            for player in self.state.ai_players()
        }


async def _auto_play_consumer(bridge: SessionBridge) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines (and the
    player auto-abstains from voting) so the flow reaches its natural
    GameOverResult ending as a smoke test.
    """
    while True:
        item = await bridge.outbox.get()
        print(item)
        if isinstance(item, GameOverResult):
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
    await _auto_play_consumer(bridge)
    await flow_task
    print(village_flow.state.model_dump_json(indent=2))


def kickoff():
    asyncio.run(_kickoff_async())


def plot():
    village_flow = VillageFlow(bridge=SessionBridge())
    village_flow.plot()


if __name__ == "__main__":
    kickoff()
