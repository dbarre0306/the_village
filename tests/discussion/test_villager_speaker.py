from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.villager_speaker import _VillagerSpeaker
from the_village.state import GameState, Player


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    return Agent(role="Stub", goal="stub", backstory="stub")


def make_villager_speaker(state: GameState, player_name: str = "A") -> _VillagerSpeaker:
    return _VillagerSpeaker(
        state, SessionBridge(), player_name, _stub_agent(), _stub_agent()
    )


def test_prompt_encourages_pressing_for_answers():
    state = make_discussion_state()
    speaker = make_villager_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "willing to voice suspicion, ask pointed questions" in prompt
