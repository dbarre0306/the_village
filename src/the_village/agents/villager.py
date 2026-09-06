from __future__ import annotations

from crewai import Agent

from the_village.core.state import Player


def _build_villager_agent(villager: Player) -> Agent:
    goal = "Determine who the werewolves are and lynch them."
    backstory = (
        f"Your name is {villager.name}. You are an ordinary villager living in a village. "
        "Tragically, there are werewolves living in your village. "
        "The werewolves are killing the ordinary villagers. "
        "You do not know who the werewolves are, and you cannot see what happens during the evening. "
        "Your goal is to help the village identify and eliminate all of the werewolves before the "
        "werewolves eliminate the villagers.  You are a real person with intelligence and emotions "
        "such as anger, shock, grief, compassion, fear, suspicion, etc."
    )
    if villager.personality:
        backstory += f" {villager.personality}"
    return Agent(
        role=f"Villager {villager.name}",
        goal=goal,
        backstory=backstory,
    )
