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
