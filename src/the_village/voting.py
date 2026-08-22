from __future__ import annotations

import logging

from crewai import Agent
from pydantic import BaseModel, Field

from the_village.discussion.discussion import (
    _format_deaths,
    _format_history,
    _living_participant_names,
    _weekday,
)
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
    lynched_days = [day for day in state.days if day.player_lynched]
    if not lynched_days:
        return "(No one has been lynched yet.)"
    return "\n".join(
        f"{day.player_lynched} was lynched by the village on {_weekday(day.day_number)}."
        for day in lynched_days
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


def cast_votes(
    state: GameState,
    agents: dict[str, Agent],
    player_vote: str | None,
) -> VoteOutcome:
    living_names = _living_participant_names(state)
    votes: list[VoteRecord] = []

    for name in living_names:
        if name == state.user_player_name:
            target = _resolve_target(player_vote, living_names, exclude=name)
        else:
            candidates = [n for n in living_names if n != name]
            prompt = _build_vote_prompt(state, candidates)
            output = agents[name].kickoff(prompt, response_format=VoteChoice)
            choice = output.pydantic or VoteChoice()
            target = _resolve_target(choice.target, living_names, exclude=name)
            if choice.target and target is None:
                logger.warning(
                    "Discarding %s's invalid vote for %r", name, choice.target
                )
        votes.append(VoteRecord(voter=name, target=target))

    state.current_day.votes.extend(votes)

    tally: dict[str, int] = {}
    for vote in votes:
        if vote.target is not None:
            tally[vote.target] = tally.get(vote.target, 0) + 1

    lynched: str | None = None
    if tally:
        top_count = max(tally.values())
        top_targets = [name for name, count in tally.items() if count == top_count]
        if len(top_targets) == 1:
            lynched = top_targets[0]

    if lynched is not None:
        player = next(p for p in state.players if p.name == lynched)
        player.is_alive = False
        state.current_day.player_lynched = lynched

    return VoteOutcome(
        day_number=state.day_number, votes=votes, tally=tally, lynched=lynched
    )
