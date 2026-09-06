from crewai import Agent

from the_village.core.bridge import SessionBridge
from the_village.discussion.werewolf_speaker import _WerewolfSpeaker
from the_village.core.state import GameState, Player


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="werewolf"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    return Agent(role="Stub", goal="stub", backstory="stub")


def make_werewolf_speaker(state: GameState, player_name: str = "A") -> _WerewolfSpeaker:
    return _WerewolfSpeaker(
        state, SessionBridge(), player_name, _stub_agent(), _stub_agent()
    )


def test_prompt_allows_inventing_ungrounded_behavior_claims():
    """Werewolves need latitude to deflect suspicion by claiming another
    player seemed nervous, evasive, or dismissive even without anything in
    the discussion to back it up -- that's the deception the role exists
    for."""
    state = make_discussion_state()
    speaker = make_werewolf_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert (
        "may freely invent misleading claims about how another player has "
        "been behaving"
        in prompt
    )


def test_prompt_still_forbids_fabricating_hard_facts():
    """Latitude to lie about demeanor doesn't extend to checkable facts --
    votes, alibis, life/death status, or direct quotes must still never be
    fabricated, since those are the "easy to spot" lies the guardrail keeps
    blocking for every role."""
    state = make_discussion_state()
    speaker = make_werewolf_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "Never fabricate a hard, checkable fact though" in prompt


def test_prompt_forbids_revealing_werewolf_identity():
    state = make_discussion_state()
    speaker = make_werewolf_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "Never tell anyone you are a werewolf" in prompt


def test_prompt_omits_the_pre_announcement_knowledge_rule_for_werewolves():
    """Werewolves legitimately know who they killed before the village finds
    out -- the villager-only rule against claiming pre-announcement
    knowledge of a death would be false for them, so it must not appear in
    their prompt."""
    state = make_discussion_state()
    speaker = make_werewolf_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "no knowledge of a death before it's discovered" not in prompt


def test_guardrail_omits_the_pre_announcement_knowledge_rule_for_werewolves():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_werewolf_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "before it was found and announced" not in description


def test_prompt_omits_the_first_night_werewolf_fear_rule_for_werewolves():
    """Werewolves know exactly why they'd be locking doors (or not) on the
    first night -- the villager-only rule against claiming pre-existing
    werewolf fear would be irrelevant/false for them."""
    state = make_discussion_state()
    assert state.day_number == 1
    speaker = make_werewolf_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "first night the village has ever had" not in prompt


def test_guardrail_omits_the_first_night_werewolf_fear_rule_for_werewolves():
    state = make_discussion_state()
    assert state.day_number == 1
    speaker = make_werewolf_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "before that first night" not in description


def test_prompt_omits_the_home_alone_rule_for_werewolves():
    """Werewolves deliberately exploit weak alibis to cast suspicion on
    innocent villagers -- the villager-only rule against treating being home
    alone as suspicious would undercut that deception."""
    state = make_discussion_state()
    speaker = make_werewolf_speaker(state)
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "Being home alone" not in prompt
