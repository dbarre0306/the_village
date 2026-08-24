from the_village.agents.werewolf import _build_werewolf_agent
from the_village.state import Player


def _players() -> list[Player]:
    return [
        Player(name="A", player_type="werewolf"),
        Player(name="B", player_type="werewolf"),
        Player(name="C", player_type="villager"),
    ]


def test_backstory_forbids_inventing_claims_about_other_players():
    agent = _build_werewolf_agent(_players()[0], _players())
    assert "never invent facts, alibis, or claims" in agent.backstory


def test_backstory_requires_suspicion_grounded_in_actual_discussion():
    agent = _build_werewolf_agent(_players()[0], _players())
    assert "grounded in something that was actually said" in agent.backstory
