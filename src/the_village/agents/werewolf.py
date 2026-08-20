from __future__ import annotations

from crewai import Agent

from the_village.state import Villager


def _build_werewolf_agent(villager: Villager, all_villagers: list[Villager]) -> Agent:
    packmate = next(
        v.name
        for v in all_villagers
        if v.player_type == "werewolf" and v.name != villager.name
    )
    goal = (
        "Blend in as an innocent villager while steering the group's "
        "suspicion toward someone else, without revealing that you're a "
        "werewolf."
    )
    backstory = (
        f"Your name is {villager.name}. You are secretly a werewolf. You are deceptive and cunning. "
        f"Your fellow werewolf is {packmate} — you know this, no one else does. You want someone else "
        "blamed for the killing, so you actively steer suspicion toward other villagers — voicing "
        "doubts about their behavior, questioning their alibi, or agreeing with and amplifying accusations "
        "others raise — all without revealing yourself or your packmate."
        "You speak the way people actually do in a tense group conversation: briefly. One or two "
        "sentences, never a speech."
    )
    return Agent(
        role=f"Villager {villager.name}",
        goal=goal,
        backstory=backstory,
    )
