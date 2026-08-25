import logging
import random
from typing import Any

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, Field

from the_village.state import GameState

logger = logging.getLogger(__name__)


class _VictimChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description="The name of the living player the pack has chosen to kill tonight.",
    )


def _eligible_targets(state: GameState) -> list[str]:
    return [
        p.name for p in state.players if p.is_alive and p.player_type != "werewolf"
    ]


def _build_guardrail(eligible: list[str]):
    def _guardrail(output: Any) -> tuple[bool, Any]:
        choice: _VictimChoice | None = output.pydantic
        target = choice.target if choice else None
        if target in eligible:
            return (True, choice)
        return (
            False,
            f"You must pick a living target from: {', '.join(eligible)}",
        )

    return _guardrail


def _ensure_living_pack_leader(state: GameState, rng: random.Random) -> None:
    living_werewolves = [
        p for p in state.players if p.player_type == "werewolf" and p.is_alive
    ]
    current_leader = next(
        (p for p in state.players if p.player_type == "werewolf" and p.is_pack_leader),
        None,
    )
    if current_leader is not None and current_leader.is_alive:
        return
    if current_leader is not None:
        current_leader.is_pack_leader = False
    rng.choice(living_werewolves).is_pack_leader = True


def _order_pack(state: GameState, rng: random.Random) -> list[str]:
    living_werewolves = [
        p for p in state.players if p.player_type == "werewolf" and p.is_alive
    ]
    leader = next(p for p in living_werewolves if p.is_pack_leader)
    non_leader_names = [p.name for p in living_werewolves if not p.is_pack_leader]
    rng.shuffle(non_leader_names)
    return non_leader_names + [leader.name]


def _build_target_prompt(state: GameState, eligible: list[str]) -> str:
    return "\n".join(
        [
            "Known facts:",
            state.format_deaths(),
            state.format_lynchings(),
            "",
            f"Living players you may target tonight: {', '.join(eligible)}.",
            "",
            "Discussion so far:",
            state.format_history(),
            "",
            "Discuss privately with your fellow werewolves and decide who "
            "the pack should kill tonight. Ground your reasoning in the "
            "Known facts and Discussion above.",
        ]
    )


def _build_tasks(
    order: list[str],
    player_agents: dict[str, Agent],
    state: GameState,
    eligible: list[str],
) -> list[Task]:
    prompt = _build_target_prompt(state, eligible)
    tasks: list[Task] = []
    for index, name in enumerate(order):
        is_decider = index == len(order) - 1
        kwargs: dict = {}
        if tasks:
            kwargs["context"] = list(tasks)
        if is_decider:
            kwargs["output_pydantic"] = _VictimChoice
            kwargs["guardrail"] = _build_guardrail(eligible)
            expected_output = "A VictimChoice naming who the pack should kill tonight."
        else:
            expected_output = "A short case for one candidate target, with reasoning."
        tasks.append(
            Task(
                description=prompt,
                agent=player_agents[name],
                expected_output=expected_output,
                **kwargs,
            )
        )
    return tasks


def _build_crew(
    tasks: list[Task], order: list[str], player_agents: dict[str, Agent]
) -> Crew:
    return Crew(
        agents=[player_agents[name] for name in order],
        tasks=tasks,
        process=Process.sequential,
    )


async def kill_next_victim(
    state: GameState,
    player_agents: dict[str, Agent],
    rng: random.Random | None = None,
) -> None:
    rng = rng or random.Random()

    _ensure_living_pack_leader(state, rng)
    order = _order_pack(state, rng)
    eligible = _eligible_targets(state)
    tasks = _build_tasks(order, player_agents, state, eligible)
    crew = _build_crew(tasks, order, player_agents)

    target = await _decide_target(crew, eligible, rng)

    victim = next(p for p in state.players if p.name == target)
    victim.is_alive = False
    state.current_day.player_found_dead = victim.name


async def _decide_target(crew: Crew, eligible: list[str], rng: random.Random) -> str:
    # A kill must always happen, so any failure here -- the guardrail
    # exhausting its retries, or any other Crew-run error -- falls back to
    # a random eligible target rather than propagating, mirroring night
    # one's unconditional random choice as the worst-case behavior.
    try:
        result = await crew.akickoff()
        choice = result.tasks_output[-1].pydantic
    except Exception:
        logger.warning(
            "Falling back to a random victim -- the werewolves' Crew run failed",
            exc_info=True,
        )
        return rng.choice(eligible)

    if choice is None or choice.target not in eligible:
        logger.warning(
            "Falling back to a random victim -- the werewolves' Crew did "
            "not produce a valid target"
        )
        return rng.choice(eligible)
    return choice.target
