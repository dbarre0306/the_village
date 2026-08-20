from __future__ import annotations

from crewai import Agent

from the_village.state import Villager


def build_werewolf_agent(villager: Villager, all_villagers: list[Villager]) -> Agent:
    packmate = next(
        v.name
        for v in all_villagers
        if v.player_type == "werewolf" and v.name != villager.name
    )
    role_knowledge = (
        f"You are secretly a werewolf. You are deceptive and cunning. "
        "Your fellow werewolf is {packmate} — "
        "you know this, no one else does. You want someone else blamed for "
        "the killing, so you actively steer suspicion toward other "
        "villagers — voicing doubts about their behavior, questioning "
        "their alibi, or agreeing with and amplifying accusations others "
        "raise — all without revealing yourself or your packmate."
    )
    goal = (
        "Blend in as an innocent villager while steering the group's "
        "suspicion toward someone else, without revealing that you're a "
        "werewolf."
    )
    backstory = (
        f"You are {villager.name}, a resident of a small village playing a game "
        f"of suspicion and survival after a neighbor was found dead. {role_knowledge} "
        "You react like a real person would — with shock, grief, anger, or "
        "suspicion as the moment calls for. You never invent facts, alibis, or "
        "claims that aren't grounded in what you actually know or what has "
        "already been said. You speak the way people actually do in a tense "
        "group conversation: briefly. One or two sentences, never a speech."
    )
    return Agent(
        role=f"Villager {villager.name}",
        goal=goal,
        backstory=backstory,
    )
