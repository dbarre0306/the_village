from __future__ import annotations

from crewai import Agent

from the_village.agents.villager import _build_villager_agent
from the_village.agents.werewolf import _build_werewolf_agent
from the_village.state import Villager


def build_agent(villager: Villager, all_villagers: list[Villager]) -> Agent:
    if villager.player_type == "werewolf":
        return _build_werewolf_agent(villager, all_villagers)
    return _build_villager_agent(villager)
