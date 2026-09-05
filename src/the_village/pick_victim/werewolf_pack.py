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

    async def kill_next_victim(self) -> None:
        self._ensure_living_pack_leader()
        eligible_targets = self._state.eligible_villagers_to_kill()
        tasks = self._build_tasks(eligible_targets)
        crew = self._build_crew(tasks)

        target = await self._decide_target(crew, eligible_targets)

        victim = next(p for p in self._state.players if p.name == target)
        victim.is_alive = False
        self._state.current_day.player_found_dead = victim.name

    def _ensure_living_pack_leader(self) -> None:
        pack_leader_name = self._state.werewolf_pack_leader_name()
        if pack_leader_name is None:
            living_werewolves = self._state.living_werewolves()
            self._rng.choice(living_werewolves).is_pack_leader = True

    def _build_tasks(
        self,
        eligible_targets: list[str],
    ) -> list[Task]:

        pack_member_tasks = self._build_tasks_for_pack_members(eligible_targets)
        pack_leader_task = self._build_task_for_pack_leader(
            pack_member_tasks, eligible_targets
        )

        return pack_member_tasks + [pack_leader_task]

    def _build_tasks_for_pack_members(self, eligible_targets: list[str]) -> list[Task]:
        pack_members = self._state.werewolf_pack_member_names()
        return list(
            map(
                lambda pack_member_name: self._to_pack_member_task(
                    pack_member_name, eligible_targets
                ),
                pack_members,
            )
        )

    def _to_pack_member_task(
        self, pack_member_name: str, eligible_targets: list[str]
    ) -> Task:
        return Task(
            description=self._pack_member_prompt(eligible_targets),
            agent=self._player_agents[pack_member_name],
            expected_output="The name of a villager along with the reason for targetting that villager.",
            async_execution=True,
        )

    def _pack_member_prompt(self, eligible_targets: list[str]) -> str:
        return "\n".join(
            [
                self._state.known_facts(),
                "",
                "# Instructions",
                "",
                f"Eligible living villagers you may target tonight: {', '.join(eligible_targets)}.",
                "",
                "From the list of eligible living villagers, select one who you think should "
                "be targeted for killing tonight. Base your reasoning only on the Known Facts. "
                "You should focus on trying to figure out which villager poses the greatest threat "
                "to you and the other werewolves.",
            ]
        )

    def _build_task_for_pack_leader(
        self, pack_member_tasks: list[Task], eligible_targets: list[str]
    ) -> Task:
        agent = self._player_agents[self._state.werewolf_pack_leader_name()]
        expected_output = "A VictimChoice naming who the pack should kill tonight."
        guardrail = self._build_guardrail(eligible_targets)
        if not pack_member_tasks:
            return Task(
                description=self._pack_leader_prompt_with_no_members(eligible_targets),
                agent=agent,
                expected_output=expected_output,
                output_pydantic=_VictimChoice,
                guardrail=guardrail,
                guardrail_max_retries=1,
            )
        return Task(
            description=self._pack_leader_prompt_with_members(eligible_targets),
            agent=agent,
            expected_output=expected_output,
            output_pydantic=_VictimChoice,
            guardrail=guardrail,
            guardrail_max_retries=1,
            context=pack_member_tasks,
        )

    def _pack_leader_prompt_with_no_members(self, eligible_targets: list[str]) -> str:
        return "\n".join(
            [
                self._state.known_facts(),
                "",
                "# Instructions",
                "",
                f"Eligible living villagers you may target tonight: {', '.join(eligible_targets)}.",
                "",
                "From the list of eligible living villagers, select one who you think should "
                "be targeted for killing tonight. Base your reasoning only on the Known Facts. "
                "You should focus on trying to figure out which villager poses the greatest threat "
                "to you.",
            ]
        )

    def _pack_leader_prompt_with_members(self, eligible_targets: list[str]) -> str:
        return "\n".join(
            [
                self._state.known_facts(),
                "",
                "# Instructions",
                "",
                f"Eligible living villagers you may target tonight: {', '.join(eligible_targets)}.",
                "",
                "From the list of eligible living villagers, select one who you think should "
                "be targeted for killing tonight. Base your reasoning only on the Known Facts and "
                "on the input from the other werewolves (their tasks).  Consider who they selected and why "
                "they selected that villager. Like the other werewolves, you should focus on trying to "
                "figure out which villager poses the greatest threat to you and the other werewolves.",
            ]
        )

    def _build_guardrail(self, eligible_targets: list[str]):
        def _guardrail(output: Any) -> tuple[bool, Any]:
            choice: _VictimChoice | None = output.pydantic
            target = choice.target if choice else None
            if target in eligible_targets:
                return (True, choice)
            return (
                False,
                f"You must pick a living target from: {', '.join(eligible_targets)}",
            )

        return _guardrail

    def _build_crew(self, tasks: list[Task]) -> Crew:
        return Crew(
            agents=[
                self._player_agents[name]
                for name in self._state.living_werewolf_names()
            ],
            tasks=tasks,
            process=Process.sequential,
        )

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
