from __future__ import annotations

from crewai import Agent

from the_village.agents.villager import build_villager_agent
from the_village.agents.werewolf import build_werewolf_agent
from the_village.state import Villager


def build_agent(villager: Villager, all_villagers: list[Villager]) -> Agent:
    if villager.player_type == "werewolf":
        return build_werewolf_agent(villager, all_villagers)
    return build_villager_agent(villager)
