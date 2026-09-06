from crewai import Agent

from the_village.core.bridge import SessionBridge
from the_village.core.state import GameState, Player
from the_village.voting.werewolf_voter import _WerewolfVoter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="werewolf"),
        Player(name="B", player_type="werewolf"),
        Player(name="C", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    return Agent(role="Stub", goal="stub", backstory="stub")


def make_werewolf_voter(state: GameState, player_name: str = "A") -> _WerewolfVoter:
    return _WerewolfVoter(state, SessionBridge(), player_name, _stub_agent())


def test_prompt_names_the_other_living_werewolf():
    state = make_voting_state()
    voter = make_werewolf_voter(state, "A")
    prompt = voter._build_vote_prompt()
    assert "The other living werewolf" in prompt
    assert "B" in prompt


def test_prompt_omits_werewolf_list_when_no_other_werewolf_is_alive():
    state = make_voting_state()
    for player in state.players:
        if player.name == "B":
            player.is_alive = False
    voter = make_werewolf_voter(state, "A")
    prompt = voter._build_vote_prompt()
    assert "The other living werewolf" not in prompt


def test_prompt_discourages_voting_for_a_packmate():
    state = make_voting_state()
    voter = make_werewolf_voter(state, "A")
    prompt = voter._build_vote_prompt()
    assert "Never vote for yourself or a fellow werewolf" in prompt
