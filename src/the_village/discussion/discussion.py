from __future__ import annotations

import logging
import random
from typing import Final

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.human_speaker import _HumanSpeaker
from the_village.discussion.reply_chain import _ReplyChain
from the_village.discussion.speaker import _Speaker
from the_village.discussion.villager_speaker import _VillagerSpeaker
from the_village.discussion.werewolf_speaker import _WerewolfSpeaker
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

    def _build_speakers(self) -> dict[str, _Speaker]:
        living_players = self._state.living_player_names()
        return {
            player_name: self._build_speaker(player_name)
            for player_name in living_players
        }

    def _build_speaker(self, player_name) -> _Speaker | None:
        if self._state.is_human_player(player_name):
            return _HumanSpeaker(
                self._state,
                self._bridge,
                player_name,
                self._analyst_agent,
            )
        if self._state.is_werewolf(player_name):
            return _WerewolfSpeaker(
                self._state,
                self._bridge,
                player_name,
                self._player_agents[player_name],
                self._analyst_agent,
            )
        return _VillagerSpeaker(
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
        living_players = self._state.living_player_names()
        self._rng.shuffle(living_players)
        last_player_to_speak = self._state.last_player_to_speak()

        if len(living_players) == 0:
            return []

        if len(living_players) == 1:
            if living_players[0] == last_player_to_speak:
                return []  # don't allow the player to speak again
            return living_players

        disallowed_first_speaker = self._disallowed_first_speaker(
            living_players, last_player_to_speak
        )
        if living_players[0] == disallowed_first_speaker:
            self._swap_first_player_with_another_player(living_players)

        return living_players

    def _disallowed_first_speaker(
        self, living_players: list[str], last_player_to_speak: str | None
    ) -> str | None:
        # If a player was the last to speak in a previous round, that player
        # must not be the first player to speak in this round. And the human
        # player must never open a discussion cold, before anyone else has
        # spoken.
        if last_player_to_speak is None and self._state.is_human_player(
            living_players[0]
        ):
            return living_players[0]
        return last_player_to_speak

    def _swap_first_player_with_another_player(self, living_players):
        swap_index = self._swap_candidate_index(living_players)
        living_players[0], living_players[swap_index] = (
            living_players[swap_index],
            living_players[0],
        )

    def _swap_candidate_index(self, living_players: list[str]) -> int:
        candidate_indexes = [
            index
            for index in range(1, len(living_players))
            if not self._state.is_human_player(living_players[index])
        ] or list(range(1, len(living_players)))
        return candidate_indexes[self._rng.randrange(0, len(candidate_indexes))]

    async def _handle_replies(self, message: DiscussionMessage | None) -> None:
        if message is None:
            return
        if message.addressed_to is None:
            return
        replyChain = _ReplyChain(self._speakers)
        await replyChain.execute(message)
