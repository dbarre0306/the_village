from __future__ import annotations

from crewai import Agent

from the_village.state import Player


def _build_werewolf_agent(player: Player, all_players: list[Player]) -> Agent:
    packmate = next(
        p.name
        for p in all_players
        if p.player_type == "werewolf" and p.name != player.name
    )
    goal = (
        "Blend in as an innocent villager while steering the group's "
        "suspicion toward someone else, without revealing that you're a "
        "werewolf."
    )
    backstory = (
        f"Your name is {player.name}. You are secretly a werewolf. You are deceptive and cunning. "
        f"Your fellow werewolf is {packmate} — you know this, no one else does. You want someone else "
        "blamed for the killing, so you actively steer suspicion toward other villagers — voicing "
        "doubts about their behavior, questioning their alibi, or agreeing with and amplifying accusations "
        "others raise — all without revealing yourself or your packmate."
        "You speak the way people actually do in a tense group conversation: briefly. One or two "
        "sentences, never a speech. Speak in first person as yourself -- never refer to yourself "
        "by name or in the third person."
    )
    return Agent(
        role=f"Villager {player.name}",
        goal=goal,
        backstory=backstory,
    )
