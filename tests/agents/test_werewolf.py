from the_village.agents.werewolf import _build_werewolf_agent
from the_village.core.state import Player


def _players() -> list[Player]:
    return [
        Player(name="A", player_type="werewolf"),
        Player(name="B", player_type="werewolf"),
        Player(name="C", player_type="villager"),
    ]


def test_backstory_establishes_identity_and_goal():
    agent = _build_werewolf_agent(_players()[0], _players())
    assert "Your name is A." in agent.backstory
    assert agent.goal == "The goal is to kill all of the villagers without them killing you."


def test_backstory_omits_packmate_sentence_when_there_is_no_packmate():
    lone_werewolf = [
        Player(name="W", player_type="werewolf"),
        Player(name="C", player_type="villager"),
    ]
    agent = _build_werewolf_agent(lone_werewolf[0], lone_werewolf)
    assert "Your fellow werewolf is" not in agent.backstory
