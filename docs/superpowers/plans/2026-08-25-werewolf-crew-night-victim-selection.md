# Werewolf Crew Night Victim Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `resolve_night`, a `Crew`-based werewolf kill decision usable on any night after the first, without touching `resolve_night_one`'s existing random, no-LLM first-night behavior.

**Architecture:** `night.py` becomes a `pick_victim/` package. `night_one.py` holds today's `resolve_night_one`, moved unchanged. `night.py` holds the new `resolve_night`: a pack-leader-succession step (plain code), a pack-ordering step (leader last, others shuffled), then a single sequential `Crew` of the living werewolves — each werewolf's `Task` chained via `context=` to every prior task, only the last (the leader's) carrying `output_pydantic=_VictimChoice` and a `guardrail` that forces a living, non-werewolf target. A `rng.choice` fallback covers the case where the `Crew` run fails outright or returns no valid choice.

**Tech Stack:** Python, crewai==1.15.16 (`Agent`, `Task`, `Crew`, `Process.sequential`, `Task.guardrail`, `Task.context`), pydantic, pytest (`asyncio_mode = "auto"`), `unittest.mock.patch`/`AsyncMock`.

**Spec:** [docs/superpowers/specs/2026-08-25-werewolf-crew-night-victim-selection-design.md](../specs/2026-08-25-werewolf-crew-night-victim-selection-design.md)

## Global Constraints

- crewai is pinned to `>=1.15.16,<2.0.0` (`pyproject.toml`) — every `Task`/`Crew`/`guardrail` usage below was verified against this installed version.
- Never use YAML files for CrewAI configuration (project-wide rule, `AGENTS.md`); this plan uses code/JSON-in-code only, consistent with that.
- Package-internals-private convention (established by `voting/` and `discussion/`): only symbols listed in a package's `__init__.py` `__all__` are importable from outside the package. Tests import private (`_`-prefixed) symbols directly from the submodule that defines them, never re-exported.
- `resolve_night_one` and its existing tests must end this plan byte-for-byte behaviorally unchanged — only its file location moves.
- `VillageFlow` wiring (looping `resolve_night` into multiple day/night cycles) and win-condition detection are explicitly out of scope for this plan (per the spec) — do not touch `village_flow.py` beyond the one import-path fix in Task 1.

---

### Task 1: Restructure `night.py` into a `pick_victim/` package

**Files:**
- Create: `src/the_village/pick_victim/__init__.py`
- Create: `src/the_village/pick_victim/night_one.py`
- Delete: `src/the_village/night.py`
- Modify: `src/the_village/village_flow.py:11` (import path only)
- Create: `tests/pick_victim/test_night_one.py`
- Delete: `tests/test_night.py`

**Interfaces:**
- Consumes: nothing new — this task only relocates existing code.
- Produces: `resolve_night_one(state: GameState, rng: random.Random | None = None) -> GameState`, now importable as `from the_village.pick_victim import resolve_night_one`. Later tasks (2-6) add sibling module `pick_victim/night.py` alongside this one.

- [ ] **Step 1: Move `night.py`'s content into the new package, unchanged**

Create `src/the_village/pick_victim/night_one.py` with exactly today's content:

```python
import random

from the_village.state import GameState


def resolve_night_one(
    state: GameState, rng: random.Random | None = None
) -> GameState:
    rng = rng or random.Random()

    eligible = [
        v for v in state.players if v.player_type == "villager" and v.is_alive
    ]
    victim = rng.choice(eligible)
    victim.is_alive = False

    state.advance_day(player_killed=victim.name)

    return state
```

- [ ] **Step 2: Create the package's public interface**

Create `src/the_village/pick_victim/__init__.py`:

```python
from .night_one import resolve_night_one

# Explicitly define ONLY the public functions allowed outside the folder
__all__ = ["resolve_night_one"]
```

- [ ] **Step 3: Delete the old module**

```bash
rm src/the_village/night.py
```

- [ ] **Step 4: Fix the one caller's import path**

In `src/the_village/village_flow.py`, change line 11:

```python
from the_village.night import resolve_night_one
```

to:

```python
from the_village.pick_victim import resolve_night_one
```

- [ ] **Step 5: Move the existing tests to the new location**

Create `tests/pick_victim/test_night_one.py` with today's `tests/test_night.py` content, only changing the import line:

```python
import random

import pytest

from the_village.pick_victim import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState, Player


def make_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="villager"),
        Player(name="D", player_type="villager"),
        Player(name="E", player_type="werewolf", is_pack_leader=True),
        Player(name="F", player_type="werewolf"),
    ]
    return GameState(user_player_name="Dana", players=players)


def test_kills_a_non_player_non_werewolf_villager():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert len(state.days) == 2
    killed_name = state.current_day.player_killed
    assert killed_name in {"A", "B", "C", "D"}


def test_killed_villager_marked_not_alive():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    killed_name = state.current_day.player_killed
    killed = next(v for v in state.players if v.name == killed_name)
    assert killed.is_alive is False


def test_player_and_werewolves_survive_night_one():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    player = next(v for v in state.players if v.player_type == "user")
    werewolves = [v for v in state.players if v.player_type == "werewolf"]
    assert player.is_alive is True
    assert all(w.is_alive for w in werewolves)


def test_day_number_advances_to_two():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert state.day_number == 2


@pytest.mark.parametrize("seed", range(50))
def test_player_is_never_killed_and_stays_alive_across_seeds(seed):
    state = build_initial_roster("Alice", random.Random(seed))
    resolve_night_one(state, random.Random(seed))

    assert state.current_day.player_killed != "Alice"
    assert state.players[0].is_alive is True
```

Then remove the old file:

```bash
rm tests/test_night.py
```

- [ ] **Step 6: Run the moved tests**

Run: `pytest tests/pick_victim/test_night_one.py -v`
Expected: all 6 tests (including the 50 parametrized cases) PASS, unchanged from before the move.

- [ ] **Step 7: Confirm nothing else references the old module path**

Run: `grep -rn "the_village\.night\b" src tests`
Expected: no output (only `the_village.pick_victim` remains).

- [ ] **Step 8: Run the full test suite**

Run: `pytest -q`
Expected: PASS, same pass count as before this task (a pure relocation, no behavior change).

- [ ] **Step 9: Commit**

```bash
git add src/the_village/pick_victim/ src/the_village/night.py src/the_village/village_flow.py tests/pick_victim/ tests/test_night.py
git commit -m "refactor: move night.py into a pick_victim/ package"
```

---

### Task 2: Victim eligibility, `_VictimChoice`, and the guardrail

**Files:**
- Create: `src/the_village/pick_victim/night.py`
- Test: `tests/pick_victim/test_night.py`

**Interfaces:**
- Consumes: `GameState`, `Player` from `the_village.state` (existing).
- Produces: `_eligible_targets(state: GameState) -> list[str]`, `_VictimChoice` (pydantic `BaseModel`, field `target: str | None`), `_build_guardrail(eligible: list[str]) -> Callable[[Any], tuple[bool, Any]]`. Later tasks (3-6) add more functions to this same file and test file.

- [ ] **Step 1: Write the failing tests**

Create `tests/pick_victim/test_night.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` (`the_village.pick_victim.night` doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `src/the_village/pick_victim/night.py`:

```python
import logging
import random
from typing import Any

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, Field

from the_village.state import GameState

logger = logging.getLogger(__name__)


class _VictimChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description="The name of the living player the pack has chosen to kill tonight.",
    )


def _eligible_targets(state: GameState) -> list[str]:
    return [
        p.name for p in state.players if p.is_alive and p.player_type != "werewolf"
    ]


def _build_guardrail(eligible: list[str]):
    def _guardrail(output: Any) -> tuple[bool, Any]:
        choice: _VictimChoice | None = output.pydantic
        target = choice.target if choice else None
        if target in eligible:
            return (True, choice)
        return (
            False,
            f"You must pick a living target from: {', '.join(eligible)}",
        )

    return _guardrail
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/pick_victim/night.py tests/pick_victim/test_night.py
git commit -m "feat: add victim eligibility and target guardrail for night crew"
```

---

### Task 3: Pack-leader succession

**Files:**
- Modify: `src/the_village/pick_victim/night.py`
- Test: `tests/pick_victim/test_night.py`

**Interfaces:**
- Consumes: `GameState`, `Player` from `the_village.state`; `make_state(werewolves, day_number=1)` test helper already defined in `tests/pick_victim/test_night.py` by Task 2.
- Produces: `_ensure_living_pack_leader(state: GameState, rng: random.Random) -> None` (mutates `state.players` in place; guarantees exactly one living werewolf has `is_pack_leader=True` afterward, assuming at least one living werewolf exists).

- [ ] **Step 1: Write the failing tests**

Add to `tests/pick_victim/test_night.py`:

```python
import random

from the_village.pick_victim.night import _ensure_living_pack_leader


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
```

(The `import random` at the top of the file collides with any earlier import in the same file — if `tests/pick_victim/test_night.py` already has a top-level `import random` from a prior task, don't duplicate it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: FAIL — `_ensure_living_pack_leader` not defined.

- [ ] **Step 3: Write the implementation**

Add to `src/the_village/pick_victim/night.py` (below `_build_guardrail`):

```python
def _ensure_living_pack_leader(state: GameState, rng: random.Random) -> None:
    living_werewolves = [
        p for p in state.players if p.player_type == "werewolf" and p.is_alive
    ]
    current_leader = next(
        (p for p in state.players if p.player_type == "werewolf" and p.is_pack_leader),
        None,
    )
    if current_leader is not None and current_leader.is_alive:
        return
    if current_leader is not None:
        current_leader.is_pack_leader = False
    rng.choice(living_werewolves).is_pack_leader = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: all 8 tests PASS (5 from Task 2 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/the_village/pick_victim/night.py tests/pick_victim/test_night.py
git commit -m "feat: promote a new pack leader in code when the leader is lynched"
```

---

### Task 4: Pack ordering (leader last)

**Files:**
- Modify: `src/the_village/pick_victim/night.py`
- Test: `tests/pick_victim/test_night.py`

**Interfaces:**
- Consumes: `GameState`, `Player`; `make_state` test helper (Task 2). Precondition documented on the function: exactly one living werewolf must already be flagged `is_pack_leader=True` (guaranteed by `_ensure_living_pack_leader`, Task 3, having run first).
- Produces: `_order_pack(state: GameState, rng: random.Random) -> list[str]` — living werewolves' names, non-leaders shuffled first, the living pack leader always last.

- [ ] **Step 1: Write the failing tests**

Add to `tests/pick_victim/test_night.py`:

```python
from the_village.pick_victim.night import _order_pack


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: FAIL — `_order_pack` not defined.

- [ ] **Step 3: Write the implementation**

Add to `src/the_village/pick_victim/night.py` (below `_ensure_living_pack_leader`):

```python
def _order_pack(state: GameState, rng: random.Random) -> list[str]:
    living_werewolves = [
        p for p in state.players if p.player_type == "werewolf" and p.is_alive
    ]
    leader = next(p for p in living_werewolves if p.is_pack_leader)
    non_leader_names = [p.name for p in living_werewolves if not p.is_pack_leader]
    rng.shuffle(non_leader_names)
    return non_leader_names + [leader.name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: all 11 tests PASS (8 from Tasks 2-3 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/the_village/pick_victim/night.py tests/pick_victim/test_night.py
git commit -m "feat: order the werewolf pack with the leader deciding last"
```

---

### Task 5: Prompt, task, and crew construction

**Files:**
- Modify: `src/the_village/pick_victim/night.py`
- Test: `tests/pick_victim/test_night.py`

**Interfaces:**
- Consumes: `_VictimChoice`, `_build_guardrail` (Task 2); `GameState.format_deaths()`, `GameState.format_lynchings()`, `GameState.format_history()` (existing, `the_village/state.py`); `Agent`, `Task`, `Crew`, `Process` from `crewai`.
- Produces: `_build_target_prompt(state: GameState, eligible: list[str]) -> str`; `_build_tasks(order: list[str], player_agents: dict[str, Agent], state: GameState, eligible: list[str]) -> list[Task]`; `_build_crew(tasks: list[Task], order: list[str], player_agents: dict[str, Agent]) -> Crew`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/pick_victim/test_night.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: FAIL — `_build_target_prompt`, `_build_tasks`, `_build_crew` not defined.

- [ ] **Step 3: Write the implementation**

Add to `src/the_village/pick_victim/night.py` (below `_order_pack`; also add `Agent`, `Crew`, `Process`, `Task` to the existing `from crewai import ...` line):

```python
def _build_target_prompt(state: GameState, eligible: list[str]) -> str:
    return "\n".join(
        [
            "Known facts:",
            state.format_deaths(),
            state.format_lynchings(),
            "",
            f"Living players you may target tonight: {', '.join(eligible)}.",
            "",
            "Discussion so far:",
            state.format_history(),
            "",
            "Discuss privately with your fellow werewolves and decide who "
            "the pack should kill tonight. Ground your reasoning in the "
            "Known facts and Discussion above.",
        ]
    )


def _build_tasks(
    order: list[str],
    player_agents: dict[str, Agent],
    state: GameState,
    eligible: list[str],
) -> list[Task]:
    prompt = _build_target_prompt(state, eligible)
    tasks: list[Task] = []
    for index, name in enumerate(order):
        is_decider = index == len(order) - 1
        kwargs: dict = {}
        if tasks:
            kwargs["context"] = list(tasks)
        if is_decider:
            kwargs["output_pydantic"] = _VictimChoice
            kwargs["guardrail"] = _build_guardrail(eligible)
            expected_output = "A VictimChoice naming who the pack should kill tonight."
        else:
            expected_output = "A short case for one candidate target, with reasoning."
        tasks.append(
            Task(
                description=prompt,
                agent=player_agents[name],
                expected_output=expected_output,
                **kwargs,
            )
        )
    return tasks


def _build_crew(
    tasks: list[Task], order: list[str], player_agents: dict[str, Agent]
) -> Crew:
    return Crew(
        agents=[player_agents[name] for name in order],
        tasks=tasks,
        process=Process.sequential,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: all 15 tests PASS (11 from Tasks 2-4 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/the_village/pick_victim/night.py tests/pick_victim/test_night.py
git commit -m "feat: build the sequential werewolf crew with chained context"
```

---

### Task 6: `resolve_night` orchestrator and public export

**Files:**
- Modify: `src/the_village/pick_victim/night.py`
- Modify: `src/the_village/pick_victim/__init__.py`
- Test: `tests/pick_victim/test_night.py`

**Interfaces:**
- Consumes: `_ensure_living_pack_leader` (Task 3), `_order_pack` (Task 4), `_eligible_targets` (Task 2), `_build_tasks`, `_build_crew` (Task 5).
- Produces: `resolve_night(state: GameState, player_agents: dict[str, Agent], rng: random.Random | None = None) -> GameState` (async), exported as `from the_village.pick_victim import resolve_night`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/pick_victim/test_night.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from the_village.pick_victim.night import resolve_night


def _crew_result(target):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=_VictimChoice(target=target))]
    )


async def test_resolve_night_kills_the_crews_chosen_target():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    state = make_state(werewolves=[leader])
    player_agents = {"W1": _stub_agent()}

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("A"))):
        await resolve_night(state, player_agents, random.Random(1))

    victim = next(p for p in state.players if p.name == "A")
    assert victim.is_alive is False
    assert state.current_day.player_killed == "A"
    assert state.day_number == 2


async def test_resolve_night_can_target_the_human_player():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    state = make_state(werewolves=[leader])
    player_agents = {"W1": _stub_agent()}

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("Dana"))):
        await resolve_night(state, player_agents, random.Random(1))

    dana = next(p for p in state.players if p.name == "Dana")
    assert dana.is_alive is False


async def test_resolve_night_falls_back_to_random_choice_when_the_crew_raises(caplog):
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    state = make_state(werewolves=[leader])
    player_agents = {"W1": _stub_agent()}

    async def _raise(*args, **kwargs):
        raise RuntimeError("guardrail retries exhausted")

    with caplog.at_level("WARNING"):
        with patch("crewai.Crew.akickoff", new=AsyncMock(side_effect=_raise)):
            await resolve_night(state, player_agents, random.Random(1))

    assert state.current_day.player_killed in {"Dana", "A", "B"}
    assert "Falling back to a random victim" in caplog.text


async def test_resolve_night_falls_back_when_pydantic_is_missing():
    leader = Player(name="W1", player_type="werewolf", is_pack_leader=True)
    state = make_state(werewolves=[leader])
    player_agents = {"W1": _stub_agent()}
    empty_result = SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=None)])

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=empty_result)):
        await resolve_night(state, player_agents, random.Random(1))

    assert state.current_day.player_killed in {"Dana", "A", "B"}


async def test_resolve_night_promotes_a_new_leader_before_deciding():
    dead_leader = Player(name="W1", player_type="werewolf", is_pack_leader=True, is_alive=False)
    packmate = Player(name="W2", player_type="werewolf")
    state = make_state(werewolves=[dead_leader, packmate])
    player_agents = {"W1": _stub_agent(), "W2": _stub_agent()}

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=_crew_result("A"))):
        await resolve_night(state, player_agents, random.Random(3))

    assert packmate.is_pack_leader is True
    assert state.current_day.player_killed == "A"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: FAIL — `resolve_night` not defined.

- [ ] **Step 3: Write the implementation**

Add to `src/the_village/pick_victim/night.py` (below `_build_crew`):

```python
async def resolve_night(
    state: GameState,
    player_agents: dict[str, Agent],
    rng: random.Random | None = None,
) -> GameState:
    rng = rng or random.Random()

    _ensure_living_pack_leader(state, rng)
    order = _order_pack(state, rng)
    eligible = _eligible_targets(state)
    tasks = _build_tasks(order, player_agents, state, eligible)
    crew = _build_crew(tasks, order, player_agents)

    target = await _decide_target(crew, eligible, rng)

    victim = next(p for p in state.players if p.name == target)
    victim.is_alive = False
    state.advance_day(player_killed=victim.name)

    return state


async def _decide_target(crew: Crew, eligible: list[str], rng: random.Random) -> str:
    # A kill must always happen, so any failure here -- the guardrail
    # exhausting its retries, or any other Crew-run error -- falls back to
    # a random eligible target rather than propagating, mirroring night
    # one's unconditional random choice as the worst-case behavior.
    try:
        result = await crew.akickoff()
        choice = result.tasks_output[-1].pydantic
    except Exception:
        choice = None

    if choice is None or choice.target not in eligible:
        logger.warning(
            "Falling back to a random victim -- the werewolves' Crew did "
            "not produce a valid target"
        )
        return rng.choice(eligible)
    return choice.target
```

Update `src/the_village/pick_victim/__init__.py` to also export `resolve_night`:

```python
from .night import resolve_night
from .night_one import resolve_night_one

# Explicitly define ONLY the public functions allowed outside the folder
__all__ = ["resolve_night", "resolve_night_one"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pick_victim/test_night.py -v`
Expected: all 20 tests PASS (15 from Tasks 2-5 + 5 new).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS — no regressions in `village_flow.py`, `voting/`, `discussion/`, or anywhere else that imports `the_village.pick_victim`.

- [ ] **Step 6: Commit**

```bash
git add src/the_village/pick_victim/night.py src/the_village/pick_victim/__init__.py tests/pick_victim/test_night.py
git commit -m "feat: add resolve_night, a Crew-based werewolf kill decision for nights after the first"
```

---

## Self-Review Notes

- **Spec coverage:** package restructure (Task 1), `_VictimChoice`/eligibility/guardrail (Task 2), pack-leader succession (Task 3), pack ordering with leader last (Task 4), prompt/task/crew construction with full accumulated context chaining (Task 5), orchestration with fallback and state mutation (Task 6) — every section of the spec (`Package Structure`, `resolve_night`'s 7 numbered steps, `Error Handling`, `Testing`) is covered by a task.
- **Placeholder scan:** no TBDs; every step shows complete, runnable code.
- **Type consistency:** `_eligible_targets`, `_build_guardrail`, `_ensure_living_pack_leader`, `_order_pack`, `_build_target_prompt`, `_build_tasks`, `_build_crew`, `_decide_target`, and `resolve_night` are named and typed identically everywhere they're introduced and later consumed.
