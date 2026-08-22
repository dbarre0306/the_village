from __future__ import annotations

import logging
import random
from typing import Final

from crewai import Agent, Crew, Process, Task
from instructor import llm_validator
from pydantic import BaseModel, Field

from the_village.bridge import FlowStatus, SessionBridge
from the_village.state import WEEKDAYS, DiscussionMessage, GameState

logger = logging.getLogger(__name__)

NUMBER_OF_ROUNDS: Final = 2

class AddressResolution(BaseModel):
    addressed_to: str | None = Field(
        default=None,
        description=(
            "The name of the living villager the message is speaking directly "
            "to, if any — e.g. asking them a question or accusing them. Leave "
            "unset if the message isn't addressing anyone in particular."
        ),
    )


def _resolve_target(candidate: str | None, state: GameState, exclude: str) -> str | None:
    if not candidate or candidate == exclude:
        return None
    if candidate not in _living_participant_names(state):
        return None
    return candidate


class TurnOutput(BaseModel):
    has_something_to_say: bool = Field(
        description=(
            "Whether you have something to say right now. False means you'll "
            "sit this turn out."
        )
    )
    message: str | None = Field(
        default=None,
        description=(
            "What you say, if you have something to say. Keep it to one or "
            "two sentences -- brief, like real spoken dialogue."
        ),
    )


def _weekday(day_number: int) -> str:
    return WEEKDAYS[(day_number - 1) % 7]


def _format_deaths(state: GameState) -> str:
    dead_days = [day for day in state.days if day.player_killed]
    if not dead_days:
        return "(No one has died yet.)"
    return "\n".join(
        f"{day.player_killed} was found dead on {_weekday(day.day_number)}."
        for day in dead_days
    )


def _format_history(state: GameState) -> str:
    messages = [message for day in state.days for message in day.discussion]
    if not messages:
        return "(No discussion has happened yet.)"
    return "\n".join(f"{m.speaker}: {m.message}" for m in messages)


def _living_participant_names(state: GameState) -> list[str]:
    return [v.name for v in state.players if v.is_alive]


DECLINED_TO_RESPOND = "[declined to respond]"


def _record_message(
    state: GameState, speaker: str, message: str, addressed_to: str | None
) -> DiscussionMessage:
    msg = DiscussionMessage(
        speaker=speaker,
        message=message,
        addressed_to=addressed_to,
    )
    state.current_day.discussion.append(msg)
    logger.debug(
        "_record_message: state=%s day=%s speaker=%s addressed_to=%s message=%r",
        id(state),
        state.day_number,
        speaker,
        addressed_to,
        message,
    )
    return msg


def _build_speak_prompt(state: GameState, addressed_by: DiscussionMessage | None) -> str:
    living_names = _living_participant_names(state)
    parts = [
        "Known facts:",
        _format_deaths(state),
        "",
        f"Living villagers: {', '.join(living_names)}, including "
        f"{state.player_name} (the human player).",
        "",
        "Discussion so far:",
        _format_history(state),
        "",
    ]
    if addressed_by is not None:
        parts.append(
            f'{addressed_by.speaker} just said to you: "{addressed_by.message}" '
            "Respond directly to this."
        )
    else:
        parts.append(
            "It's your turn. Decide whether you have something to say -- a "
            "statement, question, or accusation. If you have nothing to add, "
            "say so."
        )
    return "\n".join(parts)


def _build_analyze_prompt() -> str:
    return (
        "Determine who, if anyone, the message you were just given as "
        "context is directed at. If the speaker had nothing to say, there "
        "is nothing to analyze -- leave addressed_to unset."
    )


def _build_player_analyze_prompt(state: GameState, message: str) -> str:
    candidates = [n for n in _living_participant_names(state) if n != state.player_name]
    return "\n".join(
        [
            "Known facts:",
            _format_deaths(state),
            "",
            f"Living villagers: {', '.join(candidates)}.",
            "",
            "Discussion so far:",
            _format_history(state),
            "",
            f'{state.player_name} just said: "{message}"',
            "Who, if anyone, is this message directed at? A name that's "
            "merely mentioned doesn't count -- only someone actually being "
            "spoken to.",
        ]
    )


async def _run_ai_turn(
    speaker: Agent,
    analyst: Agent,
    state: GameState,
    name: str,
    addressed_by: DiscussionMessage | None,
) -> DiscussionMessage | None:
    speak_task = Task(
        description=_build_speak_prompt(state, addressed_by),
        agent=speaker,
        expected_output="A TurnOutput saying whether you have something to say.",
        output_pydantic=TurnOutput,
    )
    analyze_task = Task(
        description=_build_analyze_prompt(),
        agent=analyst,
        expected_output="An AddressResolution naming who, if anyone, was addressed.",
        output_pydantic=AddressResolution,
        context=[speak_task],
    )
    crew = Crew(
        agents=[speaker, analyst], tasks=[speak_task, analyze_task], process=Process.sequential
    )
    result = await crew.akickoff()
    turn = result.tasks_output[0].pydantic or TurnOutput(has_something_to_say=False)

    if not turn.has_something_to_say or not turn.message:
        if addressed_by is None:
            return None
        return _record_message(state, name, DECLINED_TO_RESPOND, addressed_to=None)

    resolution = result.tasks_output[1].pydantic or AddressResolution(addressed_to=None)
    addressed_to = _resolve_target(resolution.addressed_to, state, exclude=name)
    return _record_message(state, name, turn.message, addressed_to)


async def _resolve_player_address(
    analyst: Agent, state: GameState, message: str, exclude: str
) -> str | None:
    task = Task(
        description=_build_player_analyze_prompt(state, message),
        agent=analyst,
        expected_output="An AddressResolution naming who, if anyone, was addressed.",
        output_pydantic=AddressResolution,
    )
    crew = Crew(agents=[analyst], tasks=[task])
    result = await crew.akickoff()
    resolution = result.tasks_output[0].pydantic or AddressResolution(addressed_to=None)
    return _resolve_target(resolution.addressed_to, state, exclude=exclude)


async def _run_player_turn(
    analyst: Agent,
    state: GameState,
    bridge: SessionBridge,
    name: str,
    addressed_by: DiscussionMessage | None,
) -> DiscussionMessage | None:
    player_input = await bridge.wait_for_input()
    if player_input.message is None:
        if addressed_by is None:
            return None
        return _record_message(state, name, DECLINED_TO_RESPOND, addressed_to=None)

    addressed_to = await _resolve_player_address(
        analyst, state, player_input.message, exclude=name
    )
    return _record_message(state, name, player_input.message, addressed_to)


class DiscussionRunner:
    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_agents: dict[str, Agent],
        analyst: Agent,
        rng: random.Random | None = None,
    ):
        self.state = state
        self.bridge = bridge
        self.rng = rng or random.Random()
        self._player_agents = player_agents
        self._analyst = analyst

    async def run(self) -> list[DiscussionMessage]:
        logger.debug(
            "DiscussionRunner.run: runner=%s bridge=%s day=%s",
            id(self),
            id(self.bridge),
            self.state.day_number,
        )
        await self._run_rounds()
        return self.state.current_day.discussion


    async def _run_rounds(self):
        last_player_to_speak = None
        for round_number in range(NUMBER_OF_ROUNDS):
            logger.debug("DiscussionRunner.run: runner=%s starting round %s", id(self), round_number)
            last_player_to_speak = await self._run_round(last_player_to_speak)


    async def _run_round(self, last_player_to_speak: str|None) -> str | None:
        living_players = self._living_player_names()
        self._shuffle_players(living_players, last_player_to_speak)

        logger.debug("DiscussionRunner._run_round: runner=%s living_players=%s", id(self), living_players)

        messages = []
        spoken_via_chain: set[str] = set()
        for player in living_players:
            if player in spoken_via_chain:
                continue
            message = await self._give_player_a_turn_to_speak(player, addressed_by=None)
            if message is not None:
                messages.append(message)
                logger.debug(
                    "DiscussionRunner._run_round: runner=%s putting message=%s speaker=%s day=%s",
                    id(self),
                    id(message),
                    message.speaker,
                    self.state.day_number,
                )
                await self.bridge.outbox.put(message)
                await self._resolve_address_chain(message, spoken_via_chain=spoken_via_chain)
        return DiscussionRunner._last_player_to_speak(messages)

    
    def _living_player_names(self) -> list[str]:
        return [player.name for player in self.state.players if player.is_alive]


    def _shuffle_players(self, living_players, last_player_to_speak) -> list[str]:
        if (self._is_only_one_player_remaining_and_spoke_last(living_players, last_player_to_speak)): 
            return []
        self.rng.shuffle(living_players)

        # If a player was the last to speak in a previous round, that player
        # must not be the player to speak in this round.
        if (living_players[0] == last_player_to_speak):
            self._swap_first_player_with_another_player(living_players)
        
        return living_players


    def _swap_first_player_with_another_player(self, living_players):
        swap_index = self.rng.randrange(1, len(living_players))
        living_players[0], living_players[swap_index] = living_players[swap_index], living_players[0]


    @staticmethod
    def _is_only_one_player_remaining_and_spoke_last(living_players: list[str], last_player_to_speak: str):
        return len(living_players) == 1 and living_players[0] == last_player_to_speak


    @staticmethod
    def _last_player_to_speak(messages) -> str | None:
        index = DiscussionRunner._index_of_last_speaker(messages)
        if index >= 0:
            return messages[index].speaker
        else:
            return None

            
    @staticmethod
    def _index_of_last_speaker(messages) -> int:
        index = len(messages) - 1
        while index >= 0 and (messages[index].message is None or messages[index] == ""):
            index -= 1
        return index


    async def _give_player_a_turn_to_speak(
        self, name: str, addressed_by: DiscussionMessage | None
    ) -> DiscussionMessage | None:
        if self._is_human_player(name):
            return await self._give_human_player_a_turn_to_speak(addressed_by)
        else:
            return await _run_ai_turn(
                self._player_agents[name], self._analyst, self.state, name, addressed_by
            )


    def _is_human_player(self, name: str) -> bool:
        return name == self.state.player_name


    async def _give_human_player_a_turn_to_speak(self, addressed_by: DiscussionMessage | None) -> DiscussionMessage | None:
        status = FlowStatus.WAITING_FOR_ANSWER if addressed_by else FlowStatus.WAITING_FOR_TURN
        await self.bridge.outbox.put(status)
        return await _run_player_turn(
            self._analyst, self.state, self.bridge, self.state.player_name, addressed_by
        )

    async def _resolve_address_chain(
        self,
        message: DiscussionMessage,
        chain: frozenset[str] = frozenset(),
        spoken_via_chain: set[str] | None = None,
    ) -> None:
        logger.debug(
            "DiscussionRunner._resolve_address_chain: runner=%s speaker=%s addressed_to=%s chain=%s",
            id(self),
            message.speaker,
            message.addressed_to,
            chain,
        )
        if message.addressed_to is None:
            return
        target = message.addressed_to
        reply = await self._give_player_a_turn_to_speak(target, addressed_by=message)
        if reply is None:
            return
        if spoken_via_chain is not None:
            spoken_via_chain.add(reply.speaker)
        logger.debug(
            "DiscussionRunner._resolve_address_chain: runner=%s putting reply=%s speaker=%s day=%s",
            id(self),
            id(reply),
            reply.speaker,
            self.state.day_number,
        )
        await self.bridge.outbox.put(reply)
        chain = chain | {message.speaker, target}
        if reply.addressed_to is not None and reply.addressed_to not in chain:
            await self._resolve_address_chain(reply, chain, spoken_via_chain)
