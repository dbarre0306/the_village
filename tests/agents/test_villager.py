from the_village.agents.villager import _build_villager_agent
from the_village.state import Player


def test_backstory_establishes_identity_and_goal():
    agent = _build_villager_agent(Player(name="A", player_type="villager"))
    assert "Your name is A." in agent.backstory
    assert agent.goal == "Determine who the werewolves are and lynch them."


def test_backstory_includes_personality_when_set():
    agent = _build_villager_agent(
        Player(name="A", player_type="villager", personality="You love chaos.")
    )
    assert "You love chaos." in agent.backstory


def test_backstory_omits_personality_when_unset():
    agent = _build_villager_agent(Player(name="A", player_type="villager"))
    assert "personality" not in agent.backstory.lower()
