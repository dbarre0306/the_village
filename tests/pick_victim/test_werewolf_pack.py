import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent, Process

from the_village.pick_victim.werewolf_pack import WereWolfPack, _VictimChoice
from the_village.core.state import Day, GameState, Player


def make_state(werewolves: list[Player], day_number: int = 1) -> GameState:
    others = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    days = [Day(day_number=i) for i in range(1, day_number + 1)]
    return GameState(
        user_player_name="Dana",
        players=others + werewolves,
        days=days,
    )


def make_pack(
    werewolves: list[Player],
    player_agents: dict[str, Agent] | None = None,
    rng: random.Random | None = None,
    day_number: int = 1,
) -> WereWolfPack:
    state = make_state(werewolves, day_number=day_number)
    return WereWolfPack(state, player_agents or {}, rng)


def test_guardrail_accepts_an_eligible_target():
    pack = make_pack(werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)])
    guardrail = pack._build_guardrail(["A", "B"])
    output = SimpleNamespace(pydantic=_VictimChoice(target="A"))

    passed, result = guardrail(output)

    assert passed is True
    assert result.target == "A"


def test_guardrail_rejects_an_ineligible_target():
    pack = make_pack(werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)])
    guardrail = pack._build_guardrail(["A", "B"])
    output = SimpleNamespace(pydantic=_VictimChoice(target="W1"))

    passed, message = guardrail(output)

    assert passed is False
    assert "A, B" in message


def test_guardrail_rejects_a_missing_target():
    pack = make_pack(werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)])
    guardrail = pack._build_guardrail(["A", "B"])
    output = SimpleNamespace(pydantic=_VictimChoice(target=None))

    passed, _ = guardrail(output)

    assert passed is False


def test_ensure_living_pack_leader_replaces_a_dead_leader():
    dead_leader = Player(name="W1", player_type="werewolf", is_pack_leader=True, is_alive=False)
    packmate = Player(name="W2", player_type="werewolf")
    pack = make_pack(werewolves=[dead_leader, packmate], rng=random.Random(1))

    pack._ensure_living_pack_leader()

    assert packmate.is_pack_leader is True


def test_ensure_living_pack_leader_leaves_a_living_leader_unchanged():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    packmate = Player(name="W2", player_type="werewolf")
    pack = make_pack(werewolves=[leader, packmate], rng=random.Random(1))

    pack._ensure_living_pack_leader()

    assert leader.is_pack_leader is True
    assert packmate.is_pack_leader is False


def test_ensure_living_pack_leader_promotes_exactly_one_among_multiple_survivors():
    dead_leader = Player(name="W1", player_type="werewolf", is_pack_leader=True, is_alive=False)
    survivors = [Player(name=f"W{i}", player_type="werewolf") for i in (2, 3, 4)]
    pack = make_pack(werewolves=[dead_leader, *survivors], rng=random.Random(7))

    pack._ensure_living_pack_leader()

    new_leaders = [p for p in survivors if p.is_pack_leader]
    assert len(new_leaders) == 1


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def test_pack_member_prompt_lists_eligible_targets_and_known_facts():
    pack = make_pack(
        werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)],
        day_number=2,
    )
    pack._state.days[0].player_lynched = "C"

    prompt = pack._pack_member_prompt(["Dana", "A", "B"])

    assert "Dana, A, B" in prompt
    assert "C was lynched by the village on Monday." in prompt


def test_build_tasks_for_pack_members_creates_an_async_task_per_non_leader_werewolf():
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent(), "W3": _stub_agent()}
    pack = make_pack(
        werewolves=[
            Player(name="W1", player_type="werewolf", is_pack_leader=True),
            Player(name="W2", player_type="werewolf"),
            Player(name="W3", player_type="werewolf"),
        ],
        player_agents=player_agents,
    )

    tasks = pack._build_tasks_for_pack_members(["Dana", "A", "B"])

    assert [t.agent for t in tasks] == [player_agents["W2"], player_agents["W3"]]
    assert all(t.async_execution for t in tasks)
    assert all(t.output_pydantic is None for t in tasks)
    assert all(t.guardrail is None for t in tasks)


def test_build_tasks_for_pack_members_is_empty_with_a_single_werewolf():
    player_agents = {"W1": _stub_agent()}
    pack = make_pack(
        werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)],
        player_agents=player_agents,
    )

    assert pack._build_tasks_for_pack_members(["Dana", "A", "B"]) == []


def test_build_tasks_gives_the_leader_task_context_from_every_pack_member_task():
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent(), "W3": _stub_agent()}
    pack = make_pack(
        werewolves=[
            Player(name="W1", player_type="werewolf", is_pack_leader=True),
            Player(name="W2", player_type="werewolf"),
            Player(name="W3", player_type="werewolf"),
        ],
        player_agents=player_agents,
    )

    tasks = pack._build_tasks(["Dana", "A", "B"])

    assert len(tasks) == 3
    member_tasks, leader_task = tasks[:2], tasks[2]
    assert leader_task.agent is player_agents["W1"]
    assert leader_task.context == member_tasks
    assert leader_task.output_pydantic is _VictimChoice
    assert leader_task.guardrail is not None
    assert leader_task.guardrail_max_retries == 1


def test_build_tasks_single_werewolf_is_the_only_task_and_is_the_decider():
    player_agents = {"W1": _stub_agent()}
    pack = make_pack(
        werewolves=[Player(name="W1", player_type="werewolf", is_pack_leader=True)],
        player_agents=player_agents,
    )

    tasks = pack._build_tasks(["Dana", "A", "B"])

    assert len(tasks) == 1
    assert tasks[0].agent is player_agents["W1"]
    assert tasks[0].output_pydantic is _VictimChoice
    assert tasks[0].guardrail is not None
    assert tasks[0].guardrail_max_retries == 1


def test_build_crew_uses_sequential_process_and_all_living_werewolves_as_agents():
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent()}
    pack = make_pack(
        werewolves=[
            Player(name="W1", player_type="werewolf", is_pack_leader=True),
            Player(name="W2", player_type="werewolf"),
        ],
        player_agents=player_agents,
    )
    tasks = pack._build_tasks(["Dana", "A", "B"])

    crew = pack._build_crew(tasks)

    assert crew.process == Process.sequential
    assert crew.agents == [player_agents["W1"], player_agents["W2"]]
    assert crew.tasks == tasks


def _crew_result(target):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=_VictimChoice(target=target))]
    )


async def test_kill_next_victim_kills_the_crews_chosen_target():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    player_agents = {"W1": _stub_agent()}
    pack = make_pack(werewolves=[leader], player_agents=player_agents, rng=random.Random(1))

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("A"))):
        await pack.kill_next_victim()

    victim = next(p for p in pack._state.players if p.name == "A")
    assert victim.is_alive is False
    assert pack._state.current_day.player_found_dead == "A"
    assert pack._state.day_number == 1


async def test_kill_next_victim_can_target_the_human_player():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    player_agents = {"W1": _stub_agent()}
    pack = make_pack(werewolves=[leader], player_agents=player_agents, rng=random.Random(1))

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("Dana"))):
        await pack.kill_next_victim()

    dana = next(p for p in pack._state.players if p.name == "Dana")
    assert dana.is_alive is False


async def test_kill_next_victim_falls_back_to_random_choice_when_the_crew_raises(caplog):
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    player_agents = {"W1": _stub_agent()}
    pack = make_pack(werewolves=[leader], player_agents=player_agents, rng=random.Random(1))

    async def _raise(*args, **kwargs):
        raise RuntimeError("guardrail retries exhausted")

    with caplog.at_level("WARNING"):
        with patch("crewai.Crew.akickoff", new=AsyncMock(side_effect=_raise)):
            await pack.kill_next_victim()

    assert pack._state.current_day.player_found_dead in {"Dana", "A", "B"}
    assert "Falling back to a random victim" in caplog.text


async def test_kill_next_victim_falls_back_when_pydantic_is_missing(caplog):
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    player_agents = {"W1": _stub_agent()}
    pack = make_pack(werewolves=[leader], player_agents=player_agents, rng=random.Random(1))
    empty_result = SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=None)])

    with caplog.at_level("WARNING"):
        with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=empty_result)):
            await pack.kill_next_victim()

    assert pack._state.current_day.player_found_dead in {"Dana", "A", "B"}
    assert "Falling back to a random victim" in caplog.text


async def test_kill_next_victim_promotes_a_new_leader_before_deciding():
    dead_leader = Player(name="W1", player_type="werewolf", is_pack_leader=True, is_alive=False)
    packmate = Player(name="W2", player_type="werewolf")
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent()}
    pack = make_pack(
        werewolves=[dead_leader, packmate], player_agents=player_agents, rng=random.Random(3)
    )

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("A"))):
        await pack.kill_next_victim()

    assert packmate.is_pack_leader is True
    assert pack._state.current_day.player_found_dead == "A"
