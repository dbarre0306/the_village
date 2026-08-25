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


class WereWolfPack:

    def __init__(
        self,
        state: GameState,
        player_agents: dict[str, Agent],
        rng: random.Random = None,
    ):
        self._state = state
        self._player_agents = player_agents
        self._rng = rng or random.Random()

    def _living_werewolves(self) -> list[str]:
        return [p for p in self._state.players if p.is_werewolf and p.is_alive]

    def _pack_leader_name(self) -> str | None:
        return next(
            (p.name for p in self._living_werewolves() if p.is_pack_leader),
            None,
        )

    def _other_wolf_names(self) -> list[str]:
        return [p.name for p in self._living_werewolves() if not p.is_pack_leader]

    def _eligible_targets(self) -> list[str]:
        return [p.name for p in self._state.players if p.is_alive and p.is_not_werewolf]

    def _build_guardrail(self, eligible: list[str]):
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

    def _ensure_living_pack_leader(self) -> None:
        pack_leader_name = self._pack_leader_name()
        if pack_leader_name is None:
            living_werewolves = self._living_werewolves()
            self._rng.choice(living_werewolves).is_pack_leader = True

    def _order_pack(self) -> list[str]:
        pack_leader_name = self._pack_leader_name()
        other_wolf_names = self._other_wolf_names()
        self._rng.shuffle(other_wolf_names)
        return other_wolf_names + [pack_leader_name]

    def _build_target_prompt(self, eligible: list[str]) -> str:
        state = self._state
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
        self,
        order: list[str],
        eligible: list[str],
    ) -> list[Task]:
        prompt = self._build_target_prompt(eligible)
        tasks: list[Task] = []
        for index, name in enumerate(order):
            is_decider = index == len(order) - 1
            kwargs: dict = {}
            if tasks:
                kwargs["context"] = list(tasks)
            if is_decider:
                kwargs["output_pydantic"] = _VictimChoice
                kwargs["guardrail"] = self._build_guardrail(eligible)
                expected_output = (
                    "A VictimChoice naming who the pack should kill tonight."
                )
            else:
                expected_output = (
                    "A short case for one candidate target, with reasoning."
                )
            tasks.append(
                Task(
                    description=prompt,
                    agent=self._player_agents[name],
                    expected_output=expected_output,
                    **kwargs,
                )
            )
        return tasks

    def _build_crew(self, tasks: list[Task], order: list[str]) -> Crew:
        return Crew(
            agents=[self._player_agents[name] for name in order],
            tasks=tasks,
            process=Process.sequential,
        )

    async def kill_next_victim(self) -> None:

        self._ensure_living_pack_leader()
        order = self._order_pack()
        eligible = self._eligible_targets()
        tasks = self._build_tasks(order, eligible)
        crew = self._build_crew(tasks, order)

        target = await self._decide_target(crew, eligible)

        victim = next(p for p in self._state.players if p.name == target)
        victim.is_alive = False
        self._state.current_day.player_found_dead = victim.name

    async def _decide_target(
        self,
        crew: Crew,
        eligible: list[str],
    ) -> str:
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
            return self._rng.choice(eligible)

        if choice is None or choice.target not in eligible:
            logger.warning(
                "Falling back to a random victim -- the werewolves' Crew did "
                "not produce a valid target"
            )
            return self._rng.choice(eligible)
        return choice.target
