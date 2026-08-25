from the_village.pick_victim.night import _build_guardrail, _eligible_targets, _VictimChoice
from the_village.state import GameState, Player


def make_state(werewolves: list[Player], day_number: int = 1) -> GameState:
    others = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    from the_village.state import Day

    return GameState(
        user_player_name="Dana",
        players=others + werewolves,
        days=[Day(day_number=day_number)],
    )


def test_eligible_targets_excludes_werewolves_includes_user_and_villagers():
    state = make_state(
        werewolves=[
            Player(name="W1", player_type="werewolf", is_pack_leader=True),
            Player(name="W2", player_type="werewolf"),
        ]
    )

    assert _eligible_targets(state) == ["Dana", "A", "B"]


def test_eligible_targets_excludes_the_dead():
    state = make_state(werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)])
    dead = next(p for p in state.players if p.name == "A")
    dead.is_alive = False

    assert _eligible_targets(state) == ["Dana", "B"]


def test_guardrail_accepts_an_eligible_target():
    from types import SimpleNamespace

    guardrail = _build_guardrail(["A", "B"])
    output = SimpleNamespace(pydantic=_VictimChoice(target="A"))

    passed, result = guardrail(output)

    assert passed is True
    assert result.target == "A"


def test_guardrail_rejects_an_ineligible_target():
    from types import SimpleNamespace

    guardrail = _build_guardrail(["A", "B"])
    output = SimpleNamespace(pydantic=_VictimChoice(target="W1"))

    passed, message = guardrail(output)

    assert passed is False
    assert "A, B" in message


def test_guardrail_rejects_a_missing_target():
    from types import SimpleNamespace

    guardrail = _build_guardrail(["A", "B"])
    output = SimpleNamespace(pydantic=_VictimChoice(target=None))

    passed, _ = guardrail(output)

    assert passed is False
