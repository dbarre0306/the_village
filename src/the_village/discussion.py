from __future__ import annotations

import logging
import random

from crewai import Agent, Crew, Process, Task
from crewai.flow import Flow, start
from pydantic import BaseModel, Field

from the_village.agents.agent_factory import build_agent
from the_village.agents.conversation_analyst import build_conversation_analyst_agent
from the_village.bridge import FlowStatus, SessionBridge
from the_village.state import WEEKDAYS, DiscussionMessage, GameState

logger = logging.getLogger(__name__)

class AddressResolution(BaseModel):
    addressed_to: str | None = Field(
        default=None,
        description=(
            "The name of the living villager the message is speaking directly "
            "to, if any — e.g. asking them a question or accusing them. Leave "
            "unset if the message isn't addressing anyone in particular."
        ),
    )


def _last_speaker_today(state: GameState) -> str | None:
    today_messages = [m for m in state.discussion if m.day_number == state.day_number]
    return today_messages[-1].speaker if today_messages else None


def _avoid_immediate_repeat(
    order: list[str], last_speaker: str | None, rng: random.Random
) -> bool:
    """Reorder `order` in place so its front entry never repeats `last_speaker`.

    A bonus/direct reply lets someone speak out of turn while still sitting
    later in the round queue. Without this check, resuming the queue right
    after such a reply can immediately re-select that same person — this
    is called both when a round is built and every time the queue is about
    to hand out a turn, so a mid-round repeat gets deferred too.

    Returns True if `order` has no one else to swap in (its only entry is
    `last_speaker`), so the caller must decide what to do instead of handing
    out a turn.
    """
    if last_speaker is None or not order or order[0] != last_speaker:
        return False
    if len(order) == 1:
        return True
    swap_index = rng.randrange(1, len(order))
    order[0], order[swap_index] = order[swap_index], order[0]
    return False


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
    if not state.deaths:
        return "(No one has died yet.)"
    return "\n".join(
        f"{death.name} was found dead on {_weekday(death.day_number)}."
        for death in state.deaths
    )


def _format_history(state: GameState) -> str:
    if not state.discussion:
        return "(No discussion has happened yet.)"
    return "\n".join(f"{m.speaker}: {m.message}" for m in state.discussion)


def _living_participant_names(state: GameState) -> list[str]:
    return [v.name for v in state.villagers if v.is_alive]


DECLINED_TO_RESPOND = "[declined to respond]"


def _record_message(
    state: GameState, speaker: str, message: str, addressed_to: str | None
) -> DiscussionMessage:
    msg = DiscussionMessage(
        day_number=state.day_number,
        speaker=speaker,
        message=message,
        addressed_to=addressed_to,
    )
    state.discussion.append(msg)
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


class DiscussionFlow(Flow[GameState]):
    def __init__(self, bridge: SessionBridge, rng: random.Random | None = None):
        super().__init__()
        self.bridge = bridge
        self.rng = rng or random.Random()
        self._speaker_agents: dict[str, Agent] = {}
        self._analyst: Agent = build_conversation_analyst_agent()

    @start()
    async def run_rounds(self) -> list[DiscussionMessage]:
        living_ai = [
            v
            for v in self.state.villagers
            if v.is_alive and v.player_type in ("villager", "werewolf")
        ]
        self._speaker_agents = {
            v.name: build_agent(v, self.state.villagers) for v in living_ai
        }
        self.bridge.agents = self._speaker_agents

        for _ in range(2):
            await self._run_round()

        return self.state.discussion

    async def _run_round(self) -> None:
        order = _living_participant_names(self.state)
        self.rng.shuffle(order)
        while order:
            if _avoid_immediate_repeat(order, _last_speaker_today(self.state), self.rng):
                # The only entry left in this round would repeat the last
                # speaker and no one else is available to swap in -- skip
                # them for this round rather than force a repeat; round two
                # covers everyone again regardless.
                order.pop(0)
                continue
            name = order.pop(0)
            message = await self._run_turn(name, addressed_by=None)
            if message is not None:
                await self.bridge.outbox.put(message)
                await self._resolve_address_chain(message)

    async def _run_turn(
        self, name: str, addressed_by: DiscussionMessage | None
    ) -> DiscussionMessage | None:
        if name == self.state.player_name:
            status = FlowStatus.WAITING_FOR_ANSWER if addressed_by else FlowStatus.WAITING_FOR_TURN
            await self.bridge.outbox.put(status)
            return await _run_player_turn(
                self._analyst, self.state, self.bridge, name, addressed_by
            )
        return await _run_ai_turn(
            self._speaker_agents[name], self._analyst, self.state, name, addressed_by
        )

    async def _resolve_address_chain(
        self, message: DiscussionMessage, chain: frozenset[str] = frozenset()
    ) -> None:
        if message.addressed_to is None:
            return
        target = message.addressed_to
        reply = await self._run_turn(target, addressed_by=message)
        if reply is None:
            return
        await self.bridge.outbox.put(reply)
        chain = chain | {message.speaker, target}
        if reply.addressed_to is not None and reply.addressed_to not in chain:
            await self._resolve_address_chain(reply, chain)
