from __future__ import annotations

import logging
import random
from typing import Final

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import AiSpeaker
from the_village.discussion.human_speaker import HumanSpeaker
from the_village.discussion.reply_chain import ReplyChain
from the_village.discussion.speaker import Speaker
from the_village.state import WEEKDAYS, DiscussionMessage, GameState

logger = logging.getLogger(__name__)

NUMBER_OF_ROUNDS: Final = 2


class Discussion:
    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_agents: dict[str, Agent],
        analyst_agent: Agent,
        rng: random.Random | None = None,
    ):
        self._state = state
        self._bridge = bridge
        self._rng = rng or random.Random()
        self._player_agents = player_agents
        self._analyst_agent = analyst_agent
        self._speakers = self._build_speakers()

    def _build_speakers(self) -> dict[str, Speaker]:
        living_players = self._state.names_of_living_players()
        return {
            player_name: self._build_speaker(player_name)
            for player_name in living_players
        }

    def _build_speaker(self, player_name) -> Speaker | None:
        if self._state.is_human_player(player_name):
            return HumanSpeaker(
                self._state,
                self._bridge,
                player_name,
                self._analyst_agent,
            )
        return AiSpeaker(
            self._state,
            self._bridge,
            player_name,
            self._player_agents[player_name],
            self._analyst_agent,
        )

    async def run(self) -> list[DiscussionMessage]:
        logger.debug(
            "DiscussionRunner.run: runner=%s bridge=%s day=%s",
            id(self),
            id(self._bridge),
            self._state.day_number,
        )
        await self._run_rounds()
        return self._state.current_day.discussion

    async def _run_rounds(self):
        for round_number in range(NUMBER_OF_ROUNDS):
            logger.debug(
                "DiscussionRunner.run: runner=%s starting round %s",
                id(self),
                round_number,
            )
            await self._run_round()

    async def _run_round(self) -> None:
        living_players = self._build_shuffled_living_players()

        logger.debug(
            "Discussion._run_round: runner=%s living_players=%s",
            id(self),
            living_players,
        )

        for player in living_players:
            if not self._state.is_last_player_to_speak(player):
                await self._speak(player, addressed_by=None)

    async def _speak(
        self, player_name: str, addressed_by: DiscussionMessage | None
    ) -> None:
        speaker = self._speakers[player_name]
        message = await speaker.speak(addressed_by)
        await self._handle_replies(message)

    def _build_shuffled_living_players(self) -> list[str]:
        living_players = self._state.names_of_living_players()
        self._rng.shuffle(living_players)
        last_player_to_speak = self._state.last_player_to_speak()

        if len(living_players) == 0:
            return []

        if len(living_players) == 1:
            if living_players[0] == last_player_to_speak:
                return []  # don't allow the player to speak again
            return living_players

        # If a player was the last to speak in a previous round, that player
        # must not be the first player to speak in this round.
        if living_players[0] == last_player_to_speak:
            self._swap_first_player_with_another_player(living_players)

        return living_players

    def _swap_first_player_with_another_player(self, living_players):
        swap_index = self._rng.randrange(1, len(living_players))
        living_players[0], living_players[swap_index] = (
            living_players[swap_index],
            living_players[0],
        )

    async def _handle_replies(self, message: DiscussionMessage | None) -> None:
        if message is None:
            return
        if message.addressed_to is None:
            return
        replyChain = ReplyChain(self._speakers)
        await replyChain.execute(message)
