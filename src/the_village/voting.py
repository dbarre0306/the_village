from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from the_village.state import VoteRecord

logger = logging.getLogger(__name__)


class VoteChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "The name of the living villager you vote to lynch, or leave "
            "unset to abstain."
        ),
    )


class VoteOutcome(BaseModel):
    day_number: int
    votes: list[VoteRecord]
    tally: dict[str, int]
    lynched: str | None = None


def _resolve_target(
    candidate: str | None, living_names: list[str], exclude: str
) -> str | None:
    if not candidate or candidate == exclude:
        return None
    if candidate not in living_names:
        return None
    return candidate
