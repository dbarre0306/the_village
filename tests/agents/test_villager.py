from the_village.agents.villager import _build_villager_agent
from the_village.state import Player


def test_backstory_forbids_inventing_claims_about_other_players():
    agent = _build_villager_agent(Player(name="A", player_type="villager"))
    assert "never invent facts, alibis, or claims" in agent.backstory
