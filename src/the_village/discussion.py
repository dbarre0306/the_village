from __future__ import annotations

import random
from dataclasses import dataclass, field

from crewai import Agent

from the_village.state import GameState, Villager

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
