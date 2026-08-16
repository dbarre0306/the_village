from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

from crewai import Agent
from pydantic import BaseModel, Field

from the_village.state import DiscussionMessage, GameState, Villager

INITIAL_BUDGET = 2


@dataclass
class DiscussionRunner:
    state: GameState
    agents: dict[str, Agent]
    budgets: dict[str, int]
    passed: set[str] = field(default_factory=set)
    rng: random.Random = field(default_factory=random.Random)
    queue: list[str] = field(default_factory=list)
    awaiting_reply_from: str | None = None


def _build_agent(villager: Villager, all_villagers: list[Villager]) -> Agent:
    if villager.player_type == "werewolf":
        packmate = next(
            v.name
            for v in all_villagers
            if v.player_type == "werewolf" and v.name != villager.name
        )
        role_knowledge = (
            f"You are secretly a werewolf. Your fellow werewolf is {packmate} — "
            "you know this, no one else does. You want someone else blamed for "
            "the killing, so you actively steer suspicion toward other "
            "villagers — voicing doubts about their behavior, questioning "
            "their alibi, or agreeing with and amplifying accusations others "
            "raise — all without revealing yourself or your packmate."
        )
        goal = (
            "Blend in as an innocent villager while steering the group's "
            "suspicion toward someone else, without revealing that you're a "
            "werewolf."
        )
    else:
        role_knowledge = (
            "You are an ordinary villager. You do not know who the werewolves "
            "are, and you genuinely want to find out. You pay attention to "
            "who seems evasive, inconsistent, or too eager to point fingers, "
            "and you're willing to voice suspicion, ask pointed questions, and "
            "press others for answers."
        )
        goal = (
            "Work out who is responsible for the killing by questioning and "
            "scrutinizing the other villagers, while reacting honestly from "
            "your own perspective."
        )
    backstory = (
        f"You are {villager.name}, a resident of a small village playing a game "
        f"of suspicion and survival after a neighbor was found dead. {role_knowledge} "
        "You react like a real person would — with shock, grief, anger, or "
        "suspicion as the moment calls for. You never invent facts, alibis, or "
        "claims that aren't grounded in what you actually know or what has "
        "already been said. You speak the way people actually do in a tense "
        "group conversation: briefly. One or two sentences, never a speech."
    )
    return Agent(
        role=f"Villager {villager.name}",
        goal=goal,
        backstory=backstory,
    )


def start_discussion(
    state: GameState, rng: random.Random | None = None
) -> DiscussionRunner:
    living_ai = [
        v
        for v in state.villagers
        if v.is_alive and v.player_type in ("villager", "werewolf")
    ]
    agents = {v.name: _build_agent(v, state.villagers) for v in living_ai}
    participants = [state.player_name] + [v.name for v in living_ai]
    budgets = {name: INITIAL_BUDGET for name in participants}
    return DiscussionRunner(
        state=state, agents=agents, budgets=budgets, rng=rng or random.Random()
    )


def _active_participants(runner: DiscussionRunner) -> list[str]:
    return [
        name
        for name, budget in runner.budgets.items()
        if budget > 0 and name not in runner.passed
    ]


def _last_speaker_today(runner: DiscussionRunner) -> str | None:
    today_messages = [
        m for m in runner.state.discussion if m.day_number == runner.state.day_number
    ]
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


def _build_round(runner: DiscussionRunner) -> list[str]:
    order = _active_participants(runner)
    runner.rng.shuffle(order)
    _avoid_immediate_repeat(order, _last_speaker_today(runner), runner.rng)
    return order


def _resolve_target(
    candidate: str | None, runner: DiscussionRunner, exclude: str
) -> str | None:
    if not candidate or candidate == exclude:
        return None
    if candidate not in runner.budgets:
        return None
    if candidate in runner.passed:
        return None
    return candidate


class TurnOutput(BaseModel):
    has_something_to_say: bool = Field(
        description=(
            "Whether you have something to say right now. False means you are "
            "permanently done for the day — you will not be asked again."
        )
    )
    message: str | None = Field(
        default=None,
        description=(
            "What you say, if you have something to say. Keep it to one or "
            "two sentences — brief, like real spoken dialogue."
        ),
    )
    addressed_to: str | None = Field(
        default=None,
        description=(
            "The name of the living villager you are speaking directly to, if "
            "any — e.g. asking them a question or accusing them. Leave unset if "
            "you are not addressing anyone in particular."
        ),
    )


def _format_deaths(state: GameState) -> str:
    if not state.deaths:
        return "(No one has died yet.)"
    return "\n".join(
        f"{death.name} was found dead on day {death.day_number}."
        for death in state.deaths
    )


def _format_history(state: GameState) -> str:
    if not state.discussion:
        return "(No discussion has happened yet.)"
    return "\n".join(f"{m.speaker}: {m.message}" for m in state.discussion)


def _living_participant_names(state: GameState) -> list[str]:
    return [v.name for v in state.villagers if v.is_alive]


def _build_prompt(state: GameState, addressed_by: DiscussionMessage | None) -> str:
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
            "It's your turn. Decide whether you have something to say — a "
            "statement, question, or accusation. If you have nothing to add, "
            "say so — but note that declining means you are done for the day "
            "and will not be asked again."
        )
    return "\n".join(parts)


def _ask_agent(
    agent: Agent, state: GameState, addressed_by: DiscussionMessage | None
) -> TurnOutput:
    prompt = _build_prompt(state, addressed_by)
    output = agent.kickoff(prompt, response_format=TurnOutput)
    return output.pydantic or TurnOutput(has_something_to_say=False)


def _generate_bonus_reply(
    runner: DiscussionRunner, msg: DiscussionMessage
) -> DiscussionMessage | None:
    agent = runner.agents[msg.addressed_to]
    output = _ask_agent(agent, runner.state, addressed_by=msg)
    if not output.has_something_to_say or not output.message:
        return None
    reply = DiscussionMessage(
        day_number=runner.state.day_number,
        speaker=msg.addressed_to,
        message=output.message,
        addressed_to=_resolve_target(
            output.addressed_to, runner, exclude=msg.addressed_to
        ),
    )
    runner.state.discussion.append(reply)
    return reply


def _run_bonus_reply(
    runner: DiscussionRunner, msg: DiscussionMessage
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    """Yield a bonus reply to `msg`, pausing for player input if it addresses them.

    Returns True (via the generator's return value) if the discussion paused
    waiting on the player, so callers know to stop advancing.
    """
    bonus = _generate_bonus_reply(runner, msg)
    if bonus is None:
        return False
    yield bonus
    if bonus.addressed_to == runner.state.player_name:
        runner.awaiting_reply_from = runner.state.player_name
        yield AdvanceStatus.WAITING_FOR_ANSWER
        return True
    return False


class AdvanceStatus(str, Enum):
    WAITING_FOR_TURN = "waiting_for_turn"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    COMPLETE = "complete"


def advance(
    runner: DiscussionRunner,
    player_input: str | None = None,
    player_addressed_to: str | None = None,
    player_pass: bool = False,
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    state = runner.state
    player = state.player_name

    if runner.awaiting_reply_from == player:
        runner.awaiting_reply_from = None
        if player_input and not player_pass:
            msg = DiscussionMessage(
                day_number=state.day_number,
                speaker=player,
                message=player_input,
                addressed_to=_resolve_target(
                    player_addressed_to, runner, exclude=player
                ),
            )
            state.discussion.append(msg)
            yield msg

    elif runner.queue and runner.queue[0] == player:
        runner.queue.pop(0)
        if player_pass or not player_input:
            runner.passed.add(player)
        else:
            runner.budgets[player] -= 1
            addressed_to = _resolve_target(
                player_addressed_to, runner, exclude=player
            )
            msg = DiscussionMessage(
                day_number=state.day_number,
                speaker=player,
                message=player_input,
                addressed_to=addressed_to,
            )
            state.discussion.append(msg)
            yield msg
            if addressed_to is not None:
                paused = yield from _run_bonus_reply(runner, msg)
                if paused:
                    return

    yield from _run_ai_turns(runner)


def _run_ai_turns(
    runner: DiscussionRunner,
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    state = runner.state
    player = state.player_name

    while True:
        if not runner.queue:
            if len(_active_participants(runner)) <= 1:
                yield AdvanceStatus.COMPLETE
                return
            runner.queue = _build_round(runner)

        unresolved = _avoid_immediate_repeat(
            runner.queue, _last_speaker_today(runner), runner.rng
        )
        if unresolved:
            # The only entry left in this round's queue is the last speaker,
            # but others are still active for a future round — defer instead
            # of repeating them immediately.
            runner.queue = []
            continue

        next_name = runner.queue[0]
        if next_name == player:
            yield AdvanceStatus.WAITING_FOR_TURN
            return

        runner.queue.pop(0)
        output = _ask_agent(runner.agents[next_name], state, addressed_by=None)
        if not output.has_something_to_say or not output.message:
            runner.passed.add(next_name)
            continue

        runner.budgets[next_name] -= 1
        addressed_to = _resolve_target(output.addressed_to, runner, exclude=next_name)
        msg = DiscussionMessage(
            day_number=state.day_number,
            speaker=next_name,
            message=output.message,
            addressed_to=addressed_to,
        )
        state.discussion.append(msg)
        yield msg

        if addressed_to == player:
            runner.awaiting_reply_from = player
            yield AdvanceStatus.WAITING_FOR_ANSWER
            return
        elif addressed_to is not None:
            paused = yield from _run_bonus_reply(runner, msg)
            if paused:
                return
