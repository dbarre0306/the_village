from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

from crewai import Agent
from pydantic import BaseModel

from the_village.state import DiscussionMessage, GameState, Villager

INITIAL_BUDGET = 3


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
            "you know this, no one else does. You want to deflect suspicion "
            "without revealing yourself."
        )
    else:
        role_knowledge = (
            "You are an ordinary villager. You do not know who the werewolves are."
        )
    backstory = (
        f"You are {villager.name}, a resident of a small village playing a game "
        f"of suspicion and survival after a neighbor was found dead. {role_knowledge} "
        "You react like a real person would — with shock, grief, anger, or "
        "suspicion as the moment calls for. You never invent facts, alibis, or "
        "claims that aren't grounded in what you actually know or what has "
        "already been said."
    )
    return Agent(
        role=f"Villager {villager.name}",
        goal=(
            "Discuss the recent death honestly from your own perspective, "
            "without revealing secrets you wouldn't reveal."
        ),
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


def _build_round(runner: DiscussionRunner) -> list[str]:
    order = _active_participants(runner)
    runner.rng.shuffle(order)
    last_speaker = _last_speaker_today(runner)
    if last_speaker is not None and len(order) > 1 and order[0] == last_speaker:
        swap_index = runner.rng.randrange(1, len(order))
        order[0], order[swap_index] = order[swap_index], order[0]
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
    has_something_to_say: bool
    message: str | None = None
    addressed_to: str | None = None


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


def _build_prompt(state: GameState, addressed_by: DiscussionMessage | None) -> str:
    parts = [
        "Known facts:",
        _format_deaths(state),
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
            "statement, question, or accusation — or nothing more to add right now."
        )
    return "\n".join(parts)


def _ask_agent(
    agent: Agent, state: GameState, addressed_by: DiscussionMessage | None
) -> TurnOutput:
    prompt = _build_prompt(state, addressed_by)
    output = agent.kickoff(prompt, response_format=TurnOutput)
    return output.pydantic


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
                bonus = _generate_bonus_reply(runner, msg)
                if bonus is not None:
                    yield bonus

    yield from _run_ai_turns(runner)


def _run_ai_turns(
    runner: DiscussionRunner,
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    state = runner.state
    player = state.player_name

    while True:
        if not runner.queue:
            runner.queue = _build_round(runner)
            if not runner.queue:
                yield AdvanceStatus.COMPLETE
                return

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
            bonus = _generate_bonus_reply(runner, msg)
            if bonus is not None:
                yield bonus
