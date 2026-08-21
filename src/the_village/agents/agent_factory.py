from __future__ import annotations

from crewai import Agent

from the_village.agents.villager import _build_villager_agent
from the_village.agents.werewolf import _build_werewolf_agent
from the_village.state import Player


def build_agent(player: Player, all_players: list[Player]) -> Agent:
    if player.player_type == "werewolf":
        return _build_werewolf_agent(player, all_players)
    return _build_villager_agent(player)
