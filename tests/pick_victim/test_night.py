import random

from the_village.pick_victim.night import _build_guardrail, _eligible_targets, _VictimChoice, _ensure_living_pack_leader, _order_pack
from the_village.state import GameState, Player


def make_state(werewolves: list[Player], day_number: int = 1) -> GameState:
    others = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    from the_village.state import Day

    days = [Day(day_number=i) for i in range(1, day_number + 1)]
    return GameState(
        user_player_name="Dana",
        players=others + werewolves,
        days=days,
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


def test_ensure_living_pack_leader_replaces_a_dead_leader():
    dead_leader = Player(name="W1", player_type="werewolf", is_pack_leader=True, is_alive=False)
    packmate = Player(name="W2", player_type="werewolf")
    state = make_state(werewolves=[dead_leader, packmate])

    _ensure_living_pack_leader(state, random.Random(1))

    assert dead_leader.is_pack_leader is False
    assert packmate.is_pack_leader is True


def test_ensure_living_pack_leader_leaves_a_living_leader_unchanged():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    packmate = Player(name="W2", player_type="werewolf")
    state = make_state(werewolves=[leader, packmate])

    _ensure_living_pack_leader(state, random.Random(1))

    assert leader.is_pack_leader is True
    assert packmate.is_pack_leader is False


def test_ensure_living_pack_leader_promotes_exactly_one_among_multiple_survivors():
    dead_leader = Player(name="W1", player_type="werewolf", is_pack_leader=True, is_alive=False)
    survivors = [Player(name=f"W{i}", player_type="werewolf") for i in (2, 3, 4)]
    state = make_state(werewolves=[dead_leader, *survivors])

    _ensure_living_pack_leader(state, random.Random(7))

    new_leaders = [p for p in survivors if p.is_pack_leader]
    assert len(new_leaders) == 1


def test_order_pack_is_just_the_leader_with_a_single_werewolf():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    state = make_state(werewolves=[leader])

    order = _order_pack(state, random.Random(1))

    assert order == ["W1"]


def test_order_pack_puts_the_leader_last_with_multiple_werewolves():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    packmates = [Player(name=f"W{i}", player_type="werewolf") for i in (2, 3, 4)]
    state = make_state(werewolves=[leader, *packmates])

    order = _order_pack(state, random.Random(1))

    assert order[-1] == "W1"
    assert set(order[:-1]) == {"W2", "W3", "W4"}
    assert len(order) == 4


def test_order_pack_excludes_dead_werewolves():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    dead = Player(name="W2", player_type="werewolf", is_alive=False)
    state = make_state(werewolves=[leader, dead])

    order = _order_pack(state, random.Random(1))

    assert order == ["W1"]


from crewai import Agent, Process

from the_village.pick_victim.night import _build_crew, _build_target_prompt, _build_tasks


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def test_build_target_prompt_lists_eligible_targets_and_known_facts():
    state = make_state(
        werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)],
        day_number=2,
    )
    state.days[0].player_lynched = "C"

    prompt = _build_target_prompt(state, ["Dana", "A", "B"])

    assert "Dana, A, B" in prompt
    assert "C was lynched by the village on Sunday." in prompt


def test_build_tasks_chains_context_and_marks_only_the_last_as_decider():
    order = ["W2", "W1"]
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent()}
    state = make_state(
        werewolves=[
            Player(name="W1", player_type="werewolf", is_pack_leader=True),
            Player(name="W2", player_type="werewolf"),
        ]
    )

    tasks = _build_tasks(order, player_agents, state, ["Dana", "A", "B"])

    assert len(tasks) == 2
    assert tasks[0].agent is player_agents["W2"]
    assert tasks[0].output_pydantic is None
    assert tasks[0].guardrail is None
    assert tasks[1].agent is player_agents["W1"]
    assert tasks[1].context == [tasks[0]]
    from the_village.pick_victim.night import _VictimChoice

    assert tasks[1].output_pydantic is _VictimChoice
    assert tasks[1].guardrail is not None


def test_build_tasks_single_werewolf_is_immediately_the_decider():
    order = ["W1"]
    player_agents = {"W1": _stub_agent()}
    state = make_state(werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)])

    tasks = _build_tasks(order, player_agents, state, ["Dana", "A", "B"])

    assert len(tasks) == 1
    from the_village.pick_victim.night import _VictimChoice

    assert tasks[0].output_pydantic is _VictimChoice


def test_build_crew_uses_sequential_process_and_matching_agents():
    order = ["W2", "W1"]
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent()}
    state = make_state(
        werewolves=[
            Player(name="W1", player_type="werewolf", is_pack_leader=True),
            Player(name="W2", player_type="werewolf"),
        ]
    )
    tasks = _build_tasks(order, player_agents, state, ["Dana", "A", "B"])

    crew = _build_crew(tasks, order, player_agents)

    assert crew.process == Process.sequential
    assert crew.agents == [player_agents["W2"], player_agents["W1"]]
    assert crew.tasks == tasks
