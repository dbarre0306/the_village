from __future__ import annotations

from crewai import Agent

from the_village.state import Villager


def _build_villager_agent(villager: Villager) -> Agent:
    role_knowledge = (
        "You are an ordinary villager. You do not know who the werewolves "
        "are, and you genuinely want to find out. You pay attention to "
        "who seems evasive, inconsistent, or too eager to point fingers, "
        "and you're willing to voice suspicion, ask pointed questions, and "
        "press others for answers. You never lie or make things up unless "
        "you are afraid for your own well-being, e.g. everyone seems to think "
        "you are a werewolf."
    )
    goal = (
        "Work out who is responsible for the killing by questioning and "
        "scrutinizing the other villagers, while reacting honestly from "
        "your own perspective."
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
