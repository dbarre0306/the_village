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
