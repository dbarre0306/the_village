from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from the_village.discussion import _format_deaths, _format_history
from the_village.state import GameState, VoteRecord

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


def _format_lynchings(state: GameState) -> str:
    if not state.lynchings:
        return "(No one has been lynched yet.)"
    return "\n".join(
        f"{lynching.name} was lynched by the village on day {lynching.day_number}."
        for lynching in state.lynchings
    )


def _build_vote_prompt(state: GameState, candidates: list[str]) -> str:
    return "\n".join(
        [
            "Known facts:",
            _format_deaths(state),
            _format_lynchings(state),
            "",
            f"Living villagers you may vote to lynch: {', '.join(candidates)}.",
            "",
            "Discussion so far:",
            _format_history(state),
            "",
            "It's time to vote. Decide who you believe is responsible for the "
            "killing and vote to lynch them, or leave your vote unset to "
            "abstain. You may not vote for yourself.",
        ]
    )
