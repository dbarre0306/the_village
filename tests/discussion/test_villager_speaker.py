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


def test_prompt_forbids_claiming_pre_announcement_knowledge_of_a_death():
    state = make_discussion_state()
    speaker = make_villager_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "no knowledge of a death before it's discovered" in prompt


def test_guardrail_forbids_claiming_pre_announcement_knowledge_of_a_death():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_villager_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "before it was found and announced" in description


def test_prompt_forbids_claiming_pre_existing_werewolf_fear_on_first_night():
    state = make_discussion_state()
    assert state.day_number == 1
    speaker = make_villager_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "first night the village has ever had" in prompt


def test_prompt_allows_pre_existing_werewolf_fear_after_first_night():
    state = make_discussion_state()
    state.advance_day()
    speaker = make_villager_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "first night the village has ever had" not in prompt


def test_guardrail_forbids_claiming_pre_existing_werewolf_fear_on_first_night():
    state = make_discussion_state()
    assert state.day_number == 1
    speaker = make_villager_speaker(state)
    description = speaker._build_guardrail_description()
    assert "before that first night" in description


def test_guardrail_allows_pre_existing_werewolf_fear_after_first_night():
    state = make_discussion_state()
    state.advance_day()
    speaker = make_villager_speaker(state)
    description = speaker._build_guardrail_description()
    assert "before that first night" not in description


def test_prompt_forbids_treating_being_home_alone_as_suspicious():
    state = make_discussion_state()
    speaker = make_villager_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "Being home alone" in prompt
