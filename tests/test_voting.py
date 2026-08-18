from the_village.voting import VoteChoice, VoteOutcome, _resolve_target


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
