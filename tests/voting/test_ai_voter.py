from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.core.bridge import SessionBridge
from the_village.core.state import Day, DiscussionMessage, GameState, Player
from the_village.voting.ai_voter import _AiVoter, _VoteChoice
from the_village.voting.villager_voter import _VillagerVoter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players, days=[Day(day_number=2)])


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def _crew_result(target):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=_VoteChoice(target=target))]
    )


def make_ai_voter(state: GameState, player_name: str = "A") -> _AiVoter:
    """`_AiVoter` requires `_inner_prompt_instructions` from a concrete
    subclass -- `_VillagerVoter` stands in here since these tests exercise
    behavior shared by all voters, not villager- or werewolf-specific
    wording."""
    return _VillagerVoter(state, SessionBridge(), player_name, _stub_agent())


def test_vote_prompt_lists_other_living_candidates():
    state = make_voting_state()
    voter = make_ai_voter(state, "A")

    prompt = voter._build_vote_prompt()

    assert "Dana, B" in prompt


def test_vote_prompt_includes_lynching_history():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
            Player(name="B", player_type="villager"),
        ],
        days=[Day(day_number=1, player_lynched="C")],
    )
    voter = make_ai_voter(state, "A")

    prompt = voter._build_vote_prompt()

    assert "C was lynched by the village on Monday." in prompt


def test_vote_prompt_includes_full_multi_day_discussion_history():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
            Player(name="B", player_type="villager"),
        ],
        days=[
            Day(
                day_number=1,
                discussion=[DiscussionMessage(player_name="A", text="yesterday's claim")],
            ),
            Day(
                day_number=2,
                discussion=[DiscussionMessage(player_name="B", text="today's claim")],
            ),
        ],
    )
    voter = make_ai_voter(state, "A")

    prompt = voter._build_vote_prompt()

    assert "yesterday's claim" in prompt
    assert "today's claim" in prompt


async def test_cast_returns_the_resolved_target_from_the_scripted_choice():
    state = make_voting_state()
    voter = make_ai_voter(state, "A")

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("B"))):
        target = await voter._cast()

    assert target == "B"


async def test_cast_normalizes_a_self_vote_to_abstain():
    state = make_voting_state()
    voter = make_ai_voter(state, "A")

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("A"))):
        target = await voter._cast()

    assert target is None


async def test_cast_logs_a_warning_when_discarding_an_invalid_vote(caplog):
    state = make_voting_state()
    voter = make_ai_voter(state, "A")

    with caplog.at_level("WARNING"):
        with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("A"))):
            target = await voter._cast()

    assert target is None
    assert "Discarding A's invalid vote for 'A'" in caplog.text


async def test_cast_does_not_log_a_warning_for_a_genuine_abstention(caplog):
    state = make_voting_state()
    voter = make_ai_voter(state, "A")

    with caplog.at_level("WARNING"):
        with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result(None))):
            target = await voter._cast()

    assert target is None
    assert caplog.text == ""


async def test_cast_defaults_to_abstain_when_output_is_missing():
    state = make_voting_state()
    voter = make_ai_voter(state, "A")
    empty_result = SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=None)])

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=empty_result)):
        target = await voter._cast()

    assert target is None
