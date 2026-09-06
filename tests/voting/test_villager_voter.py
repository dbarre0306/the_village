from crewai import Agent

from the_village.core.bridge import SessionBridge
from the_village.core.state import GameState, Player
from the_village.voting.villager_voter import _VillagerVoter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    return Agent(role="Stub", goal="stub", backstory="stub")


def make_villager_voter(state: GameState, player_name: str = "A") -> _VillagerVoter:
    return _VillagerVoter(state, SessionBridge(), player_name, _stub_agent())


def test_prompt_grounds_the_vote_in_the_discussion():
    state = make_voting_state()
    voter = make_villager_voter(state)
    prompt = voter._build_vote_prompt()
    assert "who seemed evasive, inconsistent, or too eager to point fingers" in prompt
