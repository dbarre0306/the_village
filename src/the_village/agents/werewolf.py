from __future__ import annotations

from crewai import Agent

from the_village.state import Player


def _build_werewolf_agent(player: Player, all_players: list[Player]) -> Agent:
    packmate = next(
        (
            p.name
            for p in all_players
            if p.player_type == "werewolf" and p.name != player.name
        ),
        None,
    )
    goal = "The goal is to kill all of the villagers without them killing you."
    packmate_sentence = (
        f"Your fellow werewolf is {packmate} — you know this, no one else does. "
        if packmate is not None
        else ""
    )
    backstory = (
        f"Your name is {player.name}. You are secretly a werewolf. "
        f"{packmate_sentence} "
        "You are deceptive and cunning. "
        "You are good at blending in and resembling a normal villager."
    )
    return Agent(
        role=f"Villager {player.name}",
        goal=goal,
        backstory=backstory,
    )
