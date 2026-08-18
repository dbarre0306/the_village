from the_village.state import DiscussionMessage, GameState, Lynching
from the_village.voting import (
    VoteChoice,
    VoteOutcome,
    _build_vote_prompt,
    _format_lynchings,
    _resolve_target,
)


def test_resolve_target_rejects_self_vote():
    assert _resolve_target("A", ["A", "B"], exclude="A") is None


def test_resolve_target_rejects_unknown_name():
    assert _resolve_target("Ghost", ["A", "B"], exclude="A") is None


def test_resolve_target_accepts_valid_candidate():
    assert _resolve_target("B", ["A", "B"], exclude="A") == "B"


def test_resolve_target_treats_none_as_abstain():
    assert _resolve_target(None, ["A", "B"], exclude="A") is None


def test_vote_choice_defaults_to_abstain():
    assert VoteChoice().target is None


def test_vote_outcome_fields():
    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1}, lynched="A")
    assert outcome.day_number == 2
    assert outcome.tally == {"A": 1}
    assert outcome.lynched == "A"


def test_format_lynchings_with_no_lynchings():
    state = GameState(player_name="Dana")
    assert _format_lynchings(state) == "(No one has been lynched yet.)"


def test_format_lynchings_lists_each_lynching():
    state = GameState(
        player_name="Dana", lynchings=[Lynching(name="C", day_number=1)]
    )
    assert _format_lynchings(state) == "C was lynched by the village on day 1."


def test_build_vote_prompt_lists_candidates():
    state = GameState(player_name="Dana")
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "A, B" in prompt


def test_build_vote_prompt_includes_full_multi_day_discussion_history():
    state = GameState(player_name="Dana")
    state.discussion = [
        DiscussionMessage(day_number=1, speaker="A", message="yesterday's claim"),
        DiscussionMessage(day_number=2, speaker="B", message="today's claim"),
    ]
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "yesterday's claim" in prompt
    assert "today's claim" in prompt


def test_build_vote_prompt_includes_lynching_history():
    state = GameState(
        player_name="Dana", lynchings=[Lynching(name="C", day_number=1)]
    )
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "C was lynched by the village on day 1." in prompt
