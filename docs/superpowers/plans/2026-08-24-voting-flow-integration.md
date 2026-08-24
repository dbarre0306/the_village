# Voting / VillageFlow Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `src/the_village/voting.py` into a `voting/` package shaped like `discussion/`, and wire voting into `VillageFlow` as an async, bridge-paused step instead of a UI-only synchronous call.

**Architecture:** A private `_Voter` base class plus `_AiVoter`/`_HumanVoter` subclasses (mirroring `_Speaker`/`_AiSpeaker`/`_HumanSpeaker`), orchestrated by a public `Voting` class. `VillageFlow` gains a pause after discussion (gating "Begin Voting") and a `run_voting` step that runs `Voting` and puts the resulting `VoteOutcome` + a `VOTING_COMPLETE` status on the bridge. `ui.py`'s vote handlers switch from calling vote-casting logic directly to resolving/draining `SessionBridge`, the same pattern discussion's handlers already use.

**Tech Stack:** Python, crewai (`Agent`/`Crew`/`Task`, `Flow`/`@listen`/`@start`), Pydantic, Gradio, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-24-voting-flow-integration-design.md`

## Global Constraints

- Only `Voting` and `VoteOutcome` are exported from `voting/__init__.py` (`__all__`) — every other name in the package is private (leading underscore), matching `discussion/`'s convention.
- `src/the_village/voting.py` (the old flat module) and `src/the_village/voting/` (the new package) cannot coexist — Python cannot resolve `the_village.voting` unambiguously if both exist. The old file is deleted in Task 2, the moment the new package first exists.
- Deleting the old module breaks `src/the_village/ui.py` (`from the_village.voting import VoteOutcome, cast_votes`) and therefore `tests/test_ui.py`'s collection, from Task 2 until Task 7 fixes `ui.py`. This is expected and intentional — each task's own verification step scopes `pytest` to the files it touches, not the whole suite. Do not attempt to keep `test_ui.py` green before Task 7; the first point the *whole* suite is green again is the end of Task 7.
- AI vote casting uses `Crew(agents=[agent], tasks=[task]).akickoff()` (async), never synchronous `agent.kickoff()` — consistent with `_AiSpeaker`.
- The human vote is collected via `bridge.wait_for_input()` (a new `FlowStatus.WAITING_FOR_VOTE` pause), never passed into vote-casting logic as a plain argument.
- No day 2+ game loop — the flow still ends after one day, just one step later than it does today (`VOTING_COMPLETE` instead of `DISCUSSION_COMPLETE`).

---

## File Structure

- **Create** `src/the_village/voting/__init__.py` — exports `Voting`, `VoteOutcome`.
- **Create** `src/the_village/voting/voter.py` — private `_Voter` base class.
- **Create** `src/the_village/voting/ai_voter.py` — private `_AiVoter`, private `_VoteChoice`.
- **Create** `src/the_village/voting/human_voter.py` — private `_HumanVoter`.
- **Create** `src/the_village/voting/voting.py` — public `Voting`, public `VoteOutcome`.
- **Delete** `src/the_village/voting.py` (old flat module).
- **Modify** `src/the_village/bridge.py` — new `FlowStatus` members.
- **Modify** `src/the_village/village_flow.py` — new pause + `run_voting` step + fixed `_auto_play_consumer`.
- **Modify** `src/the_village/ui.py` — voting handlers rewired to drive the bridge.
- **Create** `tests/voting/test_voter.py`, `tests/voting/test_ai_voter.py`, `tests/voting/test_human_voter.py`, `tests/voting/test_voting.py`.
- **Delete** `tests/test_voting.py` (old flat test file, replaced by the four files above).
- **Modify** `tests/test_bridge.py`, `tests/test_flow.py`, `tests/test_ui.py`.

---

### Task 1: `FlowStatus` gains voting states

**Files:**
- Modify: `src/the_village/bridge.py:13-16` (the `FlowStatus` enum and `SessionBridge`'s docstring)
- Test: `tests/test_bridge.py`

**Interfaces:**
- Produces: `FlowStatus.WAITING_FOR_VOTE == "waiting_for_vote"`, `FlowStatus.VOTING_COMPLETE == "voting_complete"` — used by every later task.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bridge.py` (needs `FlowStatus` added to the existing import line):

```python
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge


def test_flow_status_includes_voting_states():
    assert FlowStatus.WAITING_FOR_VOTE == "waiting_for_vote"
    assert FlowStatus.VOTING_COMPLETE == "voting_complete"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bridge.py::test_flow_status_includes_voting_states -v`
Expected: FAIL with `AttributeError: WAITING_FOR_VOTE`

- [ ] **Step 3: Implement**

In `src/the_village/bridge.py`, change:

```python
class FlowStatus(str, Enum):
    WAITING_FOR_TURN = "waiting_for_turn"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    DISCUSSION_COMPLETE = "discussion_complete"
```

to:

```python
class FlowStatus(str, Enum):
    WAITING_FOR_TURN = "waiting_for_turn"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    WAITING_FOR_VOTE = "waiting_for_vote"
    DISCUSSION_COMPLETE = "discussion_complete"
    VOTING_COMPLETE = "voting_complete"
```

Also update `SessionBridge`'s docstring (the `outbox` line) from:

```python
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | str | FlowStatus
    -- the death announcement is a bare str, the victim's name);
```

to:

```python
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | VoteOutcome |
    str | FlowStatus -- the death announcement is a bare str, the victim's
    name; VoteOutcome is the vote reveal, put on once everyone has voted);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bridge.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/bridge.py tests/test_bridge.py
git commit -m "feat: add WAITING_FOR_VOTE and VOTING_COMPLETE flow statuses"
```

---

### Task 2: `voting/` package skeleton — `_Voter` base class

**Files:**
- Create: `src/the_village/voting/__init__.py` (empty placeholder — populated for real in Task 5)
- Create: `src/the_village/voting/voter.py`
- Delete: `src/the_village/voting.py`
- Delete: `tests/test_voting.py`
- Test: `tests/voting/test_voter.py`

**Interfaces:**
- Produces: `_Voter(state: GameState, bridge: SessionBridge, player_name: str)` with `async def cast(self) -> None`, `async def _cast(self) -> str | None` (override point), `_record_vote(self, target: str | None) -> None`, `_resolve_target(self, candidate: str | None) -> str | None`. `_AiVoter`/`_HumanVoter` (Tasks 3-4) subclass this.

- [ ] **Step 1: Delete the old module and its test file**

```bash
git rm src/the_village/voting.py tests/test_voting.py
```

This is required in this same step, not deferred: a `voting/` package and a `voting.py` module can't coexist as `the_village.voting`. From this point until Task 7, `src/the_village/ui.py` (which still does `from the_village.voting import VoteOutcome, cast_votes`) will fail to import, and `tests/test_ui.py` will fail to collect. That's expected per the Global Constraints section above — scope `pytest` invocations to the files each task actually touches until Task 7.

- [ ] **Step 2: Write the failing test**

Create `tests/voting/test_voter.py`:

```python
from the_village.bridge import SessionBridge
from the_village.state import GameState, Player
from the_village.voting.voter import _Voter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def make_voter(state: GameState, player_name: str = "A") -> _Voter:
    return _Voter(state, SessionBridge(), player_name)


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_voting_state()
    voter = make_voter(state, "A")

    assert voter._resolve_target(None) is None
    assert voter._resolve_target("A") is None
    assert voter._resolve_target("Ghost") is None
    assert voter._resolve_target("B") == "B"


def test_resolve_target_rejects_dead_villagers():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(user_player_name="Dana", players=players)
    voter = make_voter(state, "A")

    assert voter._resolve_target("B") is None


def test_record_vote_appends_to_current_day():
    state = make_voting_state()
    voter = make_voter(state, "A")

    voter._record_vote("B")

    assert len(state.current_day.votes) == 1
    record = state.current_day.votes[0]
    assert record.voter_name == "A"
    assert record.target_name == "B"


async def test_cast_calls_the_override_point_then_records_its_target():
    state = make_voting_state()

    class _StubVoter(_Voter):
        async def _cast(self):
            return "B"

    voter = _StubVoter(state, SessionBridge(), "A")
    await voter.cast()

    assert state.current_day.votes[0].voter_name == "A"
    assert state.current_day.votes[0].target_name == "B"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/voting/test_voter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.voting'` (or `voter`)

- [ ] **Step 4: Implement**

Create `src/the_village/voting/__init__.py` (temporary placeholder, replaced in Task 5):

```python
```

Create `src/the_village/voting/voter.py`:

```python
from abc import abstractmethod

from the_village.bridge import SessionBridge
from the_village.state import GameState, VoteRecord


class _Voter:

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
    ):
        self._state = state
        self._bridge = bridge
        self._player_name = player_name

    async def cast(self) -> None:
        target = await self._cast()
        self._record_vote(target)

    @abstractmethod
    async def _cast(self) -> str | None:
        pass

    def _record_vote(self, target: str | None) -> None:
        record = VoteRecord(voter_name=self._player_name, target_name=target)
        self._state.current_day.votes.append(record)

    def _resolve_target(self, candidate: str | None) -> str | None:
        if not candidate or candidate == self._player_name:
            return None
        if candidate not in self._state.names_of_living_players():
            return None
        return candidate
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/voting/test_voter.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/the_village/voting/__init__.py src/the_village/voting/voter.py tests/voting/test_voter.py
git commit -m "refactor: replace voting.py with a voting/ package, starting with _Voter"
```

---

### Task 3: `_AiVoter`

**Files:**
- Create: `src/the_village/voting/ai_voter.py`
- Test: `tests/voting/test_ai_voter.py`

**Interfaces:**
- Consumes: `_Voter` (Task 2) — `_AiVoter` subclasses it and reuses `_resolve_target`.
- Produces: `_AiVoter(state: GameState, bridge: SessionBridge, player_name: str, player_agent: Agent)`; private `_VoteChoice` (pydantic model, `target: str | None`). `Voting` (Task 5) builds one per AI villager.

- [ ] **Step 1: Write the failing test**

Create `tests/voting/test_ai_voter.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.state import Day, DiscussionMessage, GameState, Player
from the_village.voting.ai_voter import _AiVoter, _VoteChoice


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
    return _AiVoter(state, SessionBridge(), player_name, _stub_agent())


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

    assert "C was lynched by the village on Sunday." in prompt


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


async def test_cast_defaults_to_abstain_when_output_is_missing():
    state = make_voting_state()
    voter = make_ai_voter(state, "A")
    empty_result = SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=None)])

    with patch("crewai.Crew.akickoff", new=AsyncMock(return_value=empty_result)):
        target = await voter._cast()

    assert target is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/voting/test_ai_voter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.voting.ai_voter'`

- [ ] **Step 3: Implement**

Create `src/the_village/voting/ai_voter.py`:

```python
from crewai import Agent, Crew, Task
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge
from the_village.state import GameState, _weekday

from .voter import _Voter


class _VoteChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "The name of the living villager you vote to lynch, or leave "
            "unset to abstain."
        ),
    )


def _format_lynchings(state: GameState) -> str:
    lynched_days = [day for day in state.days if day.player_lynched]
    if not lynched_days:
        return "(No one has been lynched yet.)"
    return "\n".join(
        f"{day.player_lynched} was lynched by the village on {_weekday(day.day_number)}."
        for day in lynched_days
    )


class _AiVoter(_Voter):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
        player_agent: Agent,
    ):
        super().__init__(state, bridge, player_name)
        self._player_agent = player_agent

    async def _cast(self) -> str | None:
        task = self._build_vote_task()
        crew = Crew(agents=[self._player_agent], tasks=[task])
        result = await crew.akickoff()
        choice = result.tasks_output[0].pydantic or _VoteChoice()
        return self._resolve_target(choice.target)

    def _build_vote_task(self) -> Task:
        return Task(
            description=self._build_vote_prompt(),
            agent=self._player_agent,
            expected_output="A VoteChoice naming who, if anyone, to lynch.",
            output_pydantic=_VoteChoice,
        )

    def _build_vote_prompt(self) -> str:
        candidates = self._state.names_of_other_living_players(self._player_name)
        return "\n".join(
            [
                "Known facts:",
                self._state.format_deaths(),
                _format_lynchings(self._state),
                "",
                f"Living villagers you may vote to lynch: {', '.join(candidates)}.",
                "",
                "Discussion so far:",
                self._state.format_history(),
                "",
                "It's time to vote. Decide who you believe is responsible for "
                "the killing and vote to lynch them, or leave your vote unset "
                "to abstain. You may not vote for yourself.",
            ]
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/voting/test_ai_voter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting/ai_voter.py tests/voting/test_ai_voter.py
git commit -m "feat: add _AiVoter with async Crew-based vote casting"
```

---

### Task 4: `_HumanVoter`

**Files:**
- Create: `src/the_village/voting/human_voter.py`
- Test: `tests/voting/test_human_voter.py`

**Interfaces:**
- Consumes: `_Voter` (Task 2); `FlowStatus.WAITING_FOR_VOTE` (Task 1).
- Produces: `_HumanVoter(state: GameState, bridge: SessionBridge, player_name: str)`. `Voting` (Task 5) builds one for the human player.

- [ ] **Step 1: Write the failing test**

Create `tests/voting/test_human_voter.py`:

```python
import asyncio

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.state import GameState, Player
from the_village.voting.human_voter import _HumanVoter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def make_human_voter(state: GameState, bridge: SessionBridge) -> _HumanVoter:
    return _HumanVoter(state, bridge, "Dana")


async def test_puts_waiting_for_vote_before_awaiting_input():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())

    status = await bridge.outbox.get()
    assert status == FlowStatus.WAITING_FOR_VOTE

    bridge.resolve_input(PlayerInput(text=None))
    await task


async def test_resolves_target_from_player_input():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())
    await bridge.outbox.get()

    bridge.resolve_input(PlayerInput(text="A"))
    target = await task

    assert target == "A"


async def test_self_vote_normalizes_to_abstain():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())
    await bridge.outbox.get()

    bridge.resolve_input(PlayerInput(text="Dana"))
    target = await task

    assert target is None


async def test_none_input_is_abstain():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())
    await bridge.outbox.get()

    bridge.resolve_input(PlayerInput(text=None))
    target = await task

    assert target is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/voting/test_human_voter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.voting.human_voter'`

- [ ] **Step 3: Implement**

Create `src/the_village/voting/human_voter.py`:

```python
from the_village.bridge import FlowStatus, SessionBridge
from the_village.state import GameState

from .voter import _Voter


class _HumanVoter(_Voter):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
    ):
        super().__init__(state, bridge, player_name)

    async def _cast(self) -> str | None:
        await self._bridge.outbox.put(FlowStatus.WAITING_FOR_VOTE)
        player_input = await self._bridge.wait_for_input()
        return self._resolve_target(player_input.text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/voting/test_human_voter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting/human_voter.py tests/voting/test_human_voter.py
git commit -m "feat: add _HumanVoter, gathering the player's vote via the bridge"
```

---

### Task 5: `Voting` orchestrator + package exports

**Files:**
- Create: `src/the_village/voting/voting.py`
- Modify: `src/the_village/voting/__init__.py` (replace the Task 2 placeholder)
- Test: `tests/voting/test_voting.py`

**Interfaces:**
- Consumes: `_Voter`/`_AiVoter`/`_HumanVoter` (Tasks 2-4).
- Produces: `Voting(state: GameState, bridge: SessionBridge, player_agents: dict[str, Agent])` with `async def run(self) -> VoteOutcome`; public `VoteOutcome(day_number: int, votes: list[VoteRecord], tally: dict[str, int], lynched: str | None)`. `VillageFlow.run_voting` (Task 6) and `ui.py` (Task 7) both consume these.

- [ ] **Step 1: Write the failing test**

Create `tests/voting/test_voting.py`:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from crewai import Agent

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.state import Day, GameState, Player
from the_village.voting.ai_voter import _AiVoter, _VoteChoice
from the_village.voting.human_voter import _HumanVoter
from the_village.voting.voting import Voting


def _stub_agent(name: str) -> Agent:
    """A minimal real Agent, keyed by role so a scripted akickoff can tell
    which villager's vote it's answering -- Task/Crew construction
    validates that `agent` fields are actual Agent instances, so a plain
    object() won't do, even though Crew.akickoff is mocked in these tests.
    """
    return Agent(role=name, goal="stub", backstory="stub")


def _stub_agents(names: list[str]) -> dict[str, Agent]:
    return {name: _stub_agent(name) for name in names}


def _crew_result(target):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=_VoteChoice(target=target))]
    )


def _scripted_akickoff(choices: dict[str, str | None]):
    async def akickoff(crew):
        [agent] = crew.agents
        return _crew_result(choices.get(agent.role))

    return akickoff


def make_voting_state(day_number: int = 2) -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="villager"),
        Player(name="E", player_type="werewolf"),
    ]
    return GameState(
        user_player_name="Dana", players=players, days=[Day(day_number=day_number)]
    )


async def _run_voting(state: GameState, choices: dict[str, str | None], player_vote):
    agents = _stub_agents(["A", "B", "C", "E"])
    bridge = SessionBridge()
    voting = Voting(state=state, bridge=bridge, player_agents=agents)
    task = asyncio.create_task(voting.run())

    status = await bridge.outbox.get()
    assert status == FlowStatus.WAITING_FOR_VOTE
    bridge.resolve_input(PlayerInput(text=player_vote))

    with patch("crewai.Crew.akickoff", new=_scripted_akickoff(choices)):
        return await task


async def test_majority_vote_lynches_the_top_target():
    state = make_voting_state()

    outcome = await _run_voting(
        state, {"A": "B", "B": "B", "C": "B", "E": "B"}, player_vote="B"
    )

    assert outcome.lynched == "B"
    assert outcome.tally == {"B": 4}
    b = next(p for p in state.players if p.name == "B")
    assert b.is_alive is False
    assert state.current_day.player_lynched == "B"


async def test_ai_votes_can_lynch_the_player():
    state = make_voting_state()

    outcome = await _run_voting(
        state, {"A": "Dana", "B": "Dana", "C": "Dana", "E": None}, player_vote=None
    )

    assert outcome.lynched == "Dana"
    dana = next(p for p in state.players if p.name == "Dana")
    assert dana.is_alive is False
    assert state.current_day.player_lynched == "Dana"


async def test_tie_results_in_no_lynch():
    state = make_voting_state()

    # tally: A=2 (from C, E), B=2 (from Dana, A) -- tied for the top
    outcome = await _run_voting(
        state, {"A": "B", "B": "C", "C": "A", "E": "A"}, player_vote="B"
    )

    assert outcome.lynched is None
    assert state.current_day.player_lynched is None
    assert all(p.is_alive for p in state.players)


async def test_all_abstain_results_in_no_lynch():
    state = make_voting_state()

    outcome = await _run_voting(
        state, {"A": None, "B": None, "C": None, "E": None}, player_vote=None
    )

    assert outcome.lynched is None
    assert outcome.tally == {}


async def test_dead_villagers_excluded_from_voting_and_targets():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager"),
    ]
    state = GameState(user_player_name="Dana", players=players, days=[Day(day_number=3)])
    agents = _stub_agents(["B"])
    bridge = SessionBridge()
    voting = Voting(state=state, bridge=bridge, player_agents=agents)
    task = asyncio.create_task(voting.run())

    await bridge.outbox.get()
    bridge.resolve_input(PlayerInput(text=None))

    with patch("crewai.Crew.akickoff", new=_scripted_akickoff({"B": "A"})):
        outcome = await task

    assert "A" not in [record.voter_name for record in outcome.votes]
    b_record = next(v for v in outcome.votes if v.voter_name == "B")
    assert b_record.target_name is None  # A is dead, so an invalid target


async def test_votes_recorded_onto_the_current_day():
    state = make_voting_state(day_number=5)

    outcome = await _run_voting(
        state, {"A": None, "B": None, "C": None, "E": None}, player_vote=None
    )

    assert outcome.day_number == 5
    assert state.current_day.day_number == 5
    assert state.current_day.votes == outcome.votes


def test_builds_a_human_voter_for_the_user_and_ai_voters_for_everyone_else():
    state = make_voting_state()
    agents = _stub_agents(["A", "B", "C", "E"])

    voting = Voting(state=state, bridge=SessionBridge(), player_agents=agents)

    assert isinstance(voting._voters["Dana"], _HumanVoter)
    assert isinstance(voting._voters["A"], _AiVoter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/voting/test_voting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.voting.voting'`

- [ ] **Step 3: Implement**

Create `src/the_village/voting/voting.py`:

```python
from crewai import Agent
from pydantic import BaseModel

from the_village.bridge import SessionBridge
from the_village.state import GameState, VoteRecord

from .ai_voter import _AiVoter
from .human_voter import _HumanVoter
from .voter import _Voter


class VoteOutcome(BaseModel):
    day_number: int
    votes: list[VoteRecord]
    tally: dict[str, int]
    lynched: str | None = None


class Voting:

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_agents: dict[str, Agent],
    ):
        self._state = state
        self._bridge = bridge
        self._player_agents = player_agents
        self._voters = self._build_voters()

    def _build_voters(self) -> dict[str, _Voter]:
        living_players = self._state.names_of_living_players()
        return {name: self._build_voter(name) for name in living_players}

    def _build_voter(self, player_name: str) -> _Voter:
        if self._state.is_human_player(player_name):
            return _HumanVoter(self._state, self._bridge, player_name)
        return _AiVoter(
            self._state,
            self._bridge,
            player_name,
            self._player_agents[player_name],
        )

    async def run(self) -> VoteOutcome:
        # roster.py always places the human first in state.players, and
        # names_of_living_players() preserves that order -- so the human's
        # ballot naturally comes up before any AI kickoff runs, with no
        # reordering needed to keep today's instant-ballot UX.
        for player_name in self._state.names_of_living_players():
            await self._voters[player_name].cast()
        return self._tally()

    def _tally(self) -> VoteOutcome:
        votes = self._state.current_day.votes
        tally: dict[str, int] = {}
        for vote in votes:
            if vote.target_name is not None:
                tally[vote.target_name] = tally.get(vote.target_name, 0) + 1

        lynched: str | None = None
        if tally:
            top_count = max(tally.values())
            top_targets = [name for name, count in tally.items() if count == top_count]
            if len(top_targets) == 1:
                lynched = top_targets[0]

        if lynched is not None:
            player = next(p for p in self._state.players if p.name == lynched)
            player.is_alive = False
            self._state.current_day.player_lynched = lynched

        return VoteOutcome(
            day_number=self._state.day_number,
            votes=votes,
            tally=tally,
            lynched=lynched,
        )
```

Replace `src/the_village/voting/__init__.py`'s placeholder contents with:

```python
from .voting import Voting, VoteOutcome

# Explicitly define ONLY the public functions allowed outside the folder
__all__ = ["Voting", "VoteOutcome"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/voting/ -v`
Expected: PASS (every test file in the `voting/` package so far)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting/voting.py src/the_village/voting/__init__.py tests/voting/test_voting.py
git commit -m "feat: add Voting orchestrator, completing the voting/ package"
```

---

### Task 6: Wire `Voting` into `VillageFlow`

**Files:**
- Modify: `src/the_village/village_flow.py`
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: `Voting`, `VoteOutcome` (Task 5); `FlowStatus.WAITING_FOR_VOTE`/`VOTING_COMPLETE` (Task 1).
- Produces: `VillageFlow.run_voting` — a new `@listen(run_discussion)` step. After this task, the bridge sequence for a full game is: victim name → (resolve) → `[DiscussionMessage | FlowStatus.WAITING_FOR_TURN | FlowStatus.WAITING_FOR_ANSWER]*` → `FlowStatus.DISCUSSION_COMPLETE` → (resolve, new pause) → `FlowStatus.WAITING_FOR_VOTE` → (resolve) → `VoteOutcome` → `FlowStatus.VOTING_COMPLETE`.

- [ ] **Step 1: Write the failing test**

First, update the existing bridge import line in `tests/test_flow.py`. Change:

```python
from the_village.bridge import PlayerInput, SessionBridge
```

to:

```python
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
```

Then add to `tests/test_flow.py`:

```python
from types import SimpleNamespace
from unittest.mock import patch

from the_village.discussion.ai_speaker import _SpeakerOutput
from the_village.discussion.speaker import _AddressResolution
from the_village.voting import VoteOutcome
from the_village.voting.ai_voter import _VoteChoice


async def _decline_and_abstain_akickoff(crew):
    """Every AI villager declines to speak during discussion and abstains
    when voting -- a deterministic stand-in for real kickoff() calls so
    this test can drive the whole flow to completion without hitting an
    LLM. Dispatches on each task's output_pydantic, since a discussion
    turn's crew has two tasks (_SpeakerOutput, _AddressResolution) and a
    vote's crew has one (_VoteChoice)."""
    outputs = []
    for task in crew.tasks:
        if task.output_pydantic is _SpeakerOutput:
            outputs.append(_SpeakerOutput(has_something_to_say=False))
        elif task.output_pydantic is _AddressResolution:
            outputs.append(_AddressResolution(addressed_to=None))
        elif task.output_pydantic is _VoteChoice:
            outputs.append(_VoteChoice(target=None))
    return SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=o) for o in outputs])


async def test_village_flow_reaches_voting_complete_with_an_outcome():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    with patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff):
        await bridge.outbox.get()  # death announcement
        bridge.resolve_input(PlayerInput())  # -> begin discussion

        outcome = None
        while True:
            item = await bridge.outbox.get()
            if item == FlowStatus.DISCUSSION_COMPLETE:
                bridge.resolve_input(PlayerInput())  # -> begin voting
            elif item == FlowStatus.WAITING_FOR_VOTE:
                bridge.resolve_input(PlayerInput(text=None))  # player abstains
            elif isinstance(item, VoteOutcome):
                outcome = item
            elif item == FlowStatus.VOTING_COMPLETE:
                break
            elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER):
                bridge.resolve_input(PlayerInput(text=None))

        await task

    assert outcome is not None
    assert outcome.day_number == flow.state.day_number
    assert outcome.tally == {}
    assert outcome.lynched is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow.py::test_village_flow_reaches_voting_complete_with_an_outcome -v`
Expected: FAIL — the flow currently ends at `DISCUSSION_COMPLETE` with no further pause, so this test hangs/times out waiting for `WAITING_FOR_VOTE`. Run with a timeout to confirm the hang rather than waiting indefinitely: `pytest tests/test_flow.py::test_village_flow_reaches_voting_complete_with_an_outcome -v --timeout=10` (if `pytest-timeout` isn't installed, Ctrl-C after ~10s and treat that as the expected failure).

- [ ] **Step 3: Implement**

In `src/the_village/village_flow.py`, add the import:

```python
from the_village.voting import Voting
```

Change `run_discussion` from:

```python
    @listen(announce_death)
    async def run_discussion(self):
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s entering",
            id(self),
            id(self.bridge),
        )
        await Discussion(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
            analyst_agent=self._analyst_agent,
        ).run()
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s discussion runner returned, transcript len=%s",
            id(self),
            id(self.bridge),
            len(self.state.current_day.discussion),
        )
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
```

to (adding the trailing pause):

```python
    @listen(announce_death)
    async def run_discussion(self):
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s entering",
            id(self),
            id(self.bridge),
        )
        await Discussion(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
            analyst_agent=self._analyst_agent,
        ).run()
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s discussion runner returned, transcript len=%s",
            id(self),
            id(self.bridge),
            len(self.state.current_day.discussion),
        )
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
        await self.bridge.wait_for_input()

    @listen(run_discussion)
    async def run_voting(self):
        outcome = await Voting(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
        ).run()
        await self.bridge.outbox.put(outcome)
        await self.bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
```

Fix `_auto_play_consumer` — today it returns as soon as it sees `DISCUSSION_COMPLETE`, which would now leave the new "begin voting" pause (and every pause after it) unresolved forever, hanging `crewai run`/`crewai test`. Change:

```python
async def _auto_play_consumer(bridge: SessionBridge) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines so the
    flow reaches completion as a smoke test rather than hanging forever.
    """
    while True:
        item = await bridge.outbox.get()
        print(item)
        if item == FlowStatus.DISCUSSION_COMPLETE:
            return
        if item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER) or (
            not isinstance(item, FlowStatus)
        ):
            bridge.resolve_input(PlayerInput(text=None))
```

to:

```python
async def _auto_play_consumer(bridge: SessionBridge) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines (and the
    player auto-abstains from voting) so the flow reaches completion as a
    smoke test rather than hanging forever.
    """
    while True:
        item = await bridge.outbox.get()
        print(item)
        if item == FlowStatus.VOTING_COMPLETE:
            return
        if item in (
            FlowStatus.WAITING_FOR_TURN,
            FlowStatus.WAITING_FOR_ANSWER,
            FlowStatus.WAITING_FOR_VOTE,
            FlowStatus.DISCUSSION_COMPLETE,
        ) or not isinstance(item, FlowStatus):
            bridge.resolve_input(PlayerInput(text=None))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/village_flow.py tests/test_flow.py
git commit -m "feat: wire voting into VillageFlow as the step after discussion"
```

---

### Task 7: Rewire `ui.py` to drive voting through the bridge

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `Voting`, `VoteOutcome` (Task 5, already wired into `VillageFlow` by Task 6); `FlowStatus.WAITING_FOR_VOTE`/`VOTING_COMPLETE` (Task 1).
- Produces: `begin_voting(bridge, state)`, `cast_player_vote(bridge, state, target)`, `cast_player_abstain(bridge, state)` — all async generators now, replacing the old sync/direct-call versions. `format_vote_result`, `format_alive_panel`, `format_lynched_panel`, `_vote_button_updates`, `_vote_candidate_names` are unchanged.

This is the task where the whole test suite (not just files this task touches) becomes green again — Task 7's last step runs the full suite.

- [ ] **Step 1: Replace the voting section of `tests/test_ui.py`**

First, update the import block at the top of `tests/test_ui.py`. Change:

```python
from the_village.state import Day, DiscussionMessage, GameState, Player, VoteRecord
from the_village.ui import (
    begin_discussion,
    cast_player_abstain,
    cast_player_vote,
    format_alive_panel,
    format_deaths_panel,
    format_discussion_transcript,
    format_event_log,
    format_lynched_panel,
    format_vote_result,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
)
from the_village.voting import VoteChoice, VoteOutcome
```

to:

```python
from the_village.state import Day, DiscussionMessage, GameState, Player, VoteRecord
from the_village.ui import (
    begin_discussion,
    begin_voting,
    cast_player_abstain,
    cast_player_vote,
    format_alive_panel,
    format_deaths_panel,
    format_discussion_transcript,
    format_event_log,
    format_lynched_panel,
    format_vote_result,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
)
from the_village.voting import VoteOutcome
```

In `tests/test_ui.py`, `test_build_app_does_not_raise` and `test_colored_name_wraps_name_in_speaker_color_span` sit interspersed *between* the voting tests being replaced — leave both of those two functions exactly where they are and untouched. Everything else voting-related gets replaced.

First, delete `test_begin_voting_shows_vote_controls_and_hides_begin_button` entirely (the whole function, exactly as it reads today):

```python
def test_begin_voting_shows_vote_controls_and_hides_begin_button():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
    )

    outputs = ui.begin_voting(state)

    (
        begin_button_update,
        title_update,
        row_update,
        *candidate_updates,
        status_update,
        discussion_status_update,
    ) = outputs
    assert begin_button_update["visible"] is False
    assert title_update["value"] == "### Sunday's Voting"
    assert title_update["visible"] is True
    assert row_update["visible"] is True
    assert len(candidate_updates) == ui.MAX_VOTE_CANDIDATES
    assert candidate_updates[0].value == "A"
    assert candidate_updates[0].visible is True
    assert status_update["visible"] is False
    # discussion_status is left untouched so its "moderator ended the
    # discussion" message stays visible through the voting phase.
    assert "visible" not in discussion_status_update
    assert "value" not in discussion_status_update
```

replacing it with the new `begin_voting` tests (shown below), placed in that same spot, directly after `test_vote_button_updates_labels_living_candidates_and_hides_extra_slots` and before `test_build_app_does_not_raise`.

Second, delete the `ScriptedVoteAgent` class (it sits between `test_build_app_does_not_raise` and `test_colored_name_wraps_name_in_speaker_color_span` — remove only the class, keep both tests around it):

```python
class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


```

(delete it and the blank lines around it; nothing replaces it — `Crew.akickoff` patching replaces the need for a scripted agent object).

Third, delete these four functions entirely — `test_cast_player_vote_hides_controls_before_blocking_call`, `test_cast_player_vote_reveals_outcome_and_updates_panels`, `test_cast_player_vote_wraps_unexpected_errors_as_gr_error`, and `test_cast_player_abstain_records_no_target` (everything from `def test_cast_player_vote_hides_controls_before_blocking_call():` through the end of the file):

```python
def test_cast_player_vote_hides_controls_before_blocking_call():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    events = cast_player_vote(state, bridge, "A")
    first_event = next(events)

    row_update, status_update, _, _ = first_event
    assert row_update["visible"] is False
    assert status_update["value"] == "Tallying the votes…"


def test_cast_player_vote_reveals_outcome_and_updates_panels():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    events = list(cast_player_vote(state, bridge, "A"))
    _, status_update, alive_panel_value, lynched_panel_value = events[-1]

    assert f"{ui._colored_name('A', state)} was lynched by the village." in status_update["value"]
    assert "Dana" in alive_panel_value
    # Target the actual chip markup for "A" rather than a bare substring
    # check -- "A" alone would also match unrelated text/markup.
    assert '>A</span>' not in alive_panel_value
    assert '>A</span>' in lynched_panel_value


def test_cast_player_vote_wraps_unexpected_errors_as_gr_error():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )

    class BoomAgent:
        def kickoff(self, *args, **kwargs):
            raise RuntimeError("boom")

    bridge = SessionBridge(player_agents={"A": BoomAgent()})

    events = cast_player_vote(state, bridge, "A")
    next(events)  # first yield: hides the ballot before the blocking call
    # The error path must restore the ballot so the player can actually
    # retry -- leaving it hidden after the error would strand them with no
    # way to vote again.
    row_update, status_update, _, _ = next(events)
    assert row_update["visible"] is True
    assert status_update["visible"] is True

    with pytest.raises(gr.Error):
        next(events)


def test_cast_player_abstain_records_no_target():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    list(cast_player_abstain(state, bridge))

    dana_record = next(v for v in state.current_day.votes if v.voter_name == "Dana")
    assert dana_record.target_name is None
```

replacing it with the new `cast_player_vote`/`cast_player_abstain` tests (shown below), placed after `test_format_vote_result_reports_no_votes` at the end of the file.

Putting it together: insert this first block (the `begin_voting` tests) at insertion point 1 — directly after `test_vote_button_updates_labels_living_candidates_and_hides_extra_slots` and before `test_build_app_does_not_raise`:

```python
async def test_begin_voting_resolves_the_discussion_gate_and_reveals_the_ballot():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1)],
    )
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.WAITING_FOR_VOTE)

    outputs = [update async for update in begin_voting(bridge, state)]

    assert await waiter == PlayerInput()
    (
        _bridge,
        begin_button_update,
        title_update,
        row_update,
        *candidate_updates,
        status_update,
    ) = outputs[-1]
    assert begin_button_update["visible"] is False
    assert title_update["value"] == "### Sunday's Voting"
    assert title_update["visible"] is True
    assert row_update["visible"] is True
    assert len(candidate_updates) == ui.MAX_VOTE_CANDIDATES
    assert candidate_updates[0].value == "A"
    assert candidate_updates[0].visible is True
    assert status_update["visible"] is False


async def test_begin_voting_is_a_noop_when_already_resolved():
    bridge = SessionBridge()  # nothing pending -- simulates a double-click
    outputs = [update async for update in begin_voting(bridge, GameState())]
    assert outputs == []


async def test_begin_voting_raises_gr_error_on_flow_failed():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        async for _ in begin_voting(bridge, GameState()):
            pass

    waiter.cancel()
```

The four `test_format_vote_result_*` functions that already sit right after this point in the file (`test_format_vote_result_lists_breakdown_and_lynch_outcome`, `..._omits_tally_line_when_no_non_abstain_votes`, `..._reports_tie`, `..._reports_no_votes`) are unchanged — leave them exactly as they are; nothing about `format_vote_result` itself changed.

Insert this second block (a new `_voting_state` fixture helper plus the `cast_player_vote`/`cast_player_abstain` tests) at insertion point 2 — after `test_format_vote_result_reports_no_votes`, at the very end of the file:

```python
def _voting_state() -> GameState:
    return GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )


async def test_cast_player_vote_resolves_the_ballot_and_hides_controls_before_the_reveal():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    events = cast_player_vote(bridge, state, "A")
    first_event = await events.__anext__()

    assert await waiter == PlayerInput(text="A")
    row_update, status_update, _, _ = first_event
    assert row_update["visible"] is False
    assert status_update["value"] == "Tallying the votes…"
    await events.aclose()


async def test_cast_player_vote_reveals_outcome_and_updates_panels():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1}, lynched="A")

    async def feed_outcome():
        await bridge.outbox.put(outcome)
        await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

    events = cast_player_vote(bridge, state, "A")
    await events.__anext__()  # the "Tallying..." yield; also resolves waiter
    await waiter
    await feed_outcome()
    _row_update, status_update, alive_panel_value, lynched_panel_value = (
        await events.__anext__()
    )

    assert f"{ui._colored_name('A', state)} was lynched by the village." in status_update["value"]
    assert "Dana" in alive_panel_value
    assert '>A</span>' not in alive_panel_value
    assert '>A</span>' in lynched_panel_value
    await events.aclose()


async def test_cast_player_vote_raises_gr_error_on_flow_failed():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    events = cast_player_vote(bridge, state, "A")
    await events.__anext__()
    await waiter
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        await events.__anext__()


async def test_cast_player_abstain_resolves_with_no_target():
    state = _voting_state()
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    events = cast_player_abstain(bridge, state)
    await events.__anext__()

    assert await waiter == PlayerInput(text=None)
    await events.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui.py -v`
Expected: FAIL — `ImportError: cannot import name 'begin_voting'` (or similar), since `ui.py` hasn't changed yet.

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`, change the import line:

```python
from the_village.voting import VoteOutcome, cast_votes
```

to:

```python
from the_village.voting import VoteOutcome
```

Replace `begin_voting` through the end of `cast_player_abstain` (everything from `def begin_voting(state: GameState):` through the end of `def cast_player_abstain(state: GameState, bridge: SessionBridge):`) with:

```python
async def begin_voting(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput()):
        return  # already resolved (e.g. double-click) -- no-op
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    yield (
        bridge,
        gr.update(visible=False),  # begin_voting_button
        gr.update(value=f"### {weekday}'s Voting", visible=True),  # voting_title
        gr.update(),  # vote_button_row (shown once WAITING_FOR_VOTE arrives)
        *([gr.update()] * MAX_VOTE_CANDIDATES),
        gr.update(),  # vote_status
    )
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if item == FlowStatus.WAITING_FOR_VOTE:
                yield (
                    bridge,
                    gr.update(),
                    gr.update(),
                    gr.update(visible=True),
                    *_vote_button_updates(state),
                    gr.update(visible=False),
                )
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Voting turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


def format_vote_result(state: GameState, outcome: VoteOutcome) -> str:
    lines = [
        f"{_colored_name(record.voter_name, state)} voted for "
        f"{_colored_name(record.target_name, state)}."
        if record.target_name is not None
        else f"{_colored_name(record.voter_name, state)} abstained."
        for record in outcome.votes
    ]
    lines.append("")
    if outcome.tally:
        lines.append(
            "  ·  ".join(
                f"{_colored_name(name, state)}: {count}"
                for name, count in sorted(
                    outcome.tally.items(), key=lambda kv: (-kv[1], kv[0])
                )
            )
        )
        lines.append("")
    if outcome.lynched is not None:
        lines.append(f"**{_colored_name(outcome.lynched, state)} was lynched by the village.**")
    elif outcome.tally:
        lines.append("**The vote was tied — no one was lynched.**")
    else:
        lines.append("**No one voted to lynch anyone — no one was lynched.**")
    return "\n\n".join(lines)


async def cast_player_vote(bridge: SessionBridge, state: GameState, target: str | None):
    if not bridge.resolve_input(PlayerInput(text=target)):
        return
    # Hide the ballot the instant the player votes, before the AI kickoffs
    # that Voting.run() drives inside the background Flow task resolve.
    yield (
        gr.update(visible=False),
        gr.update(value="Tallying the votes…", visible=True),
        gr.update(),
        gr.update(),
    )
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, VoteOutcome):
                yield (
                    gr.update(),
                    gr.update(value=format_vote_result(state, item), visible=True),
                    format_alive_panel(state),
                    format_lynched_panel(state),
                )
            elif item == FlowStatus.VOTING_COMPLETE:
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Vote casting failed")
        raise gr.Error("Something went wrong, please try again.") from exc


async def cast_player_abstain(bridge: SessionBridge, state: GameState):
    async for update in cast_player_vote(bridge, state, None):
        yield update
```

Note `format_vote_result` is unchanged — it's included above only to show it sits between `begin_voting` and `cast_player_vote` in the file; don't modify its body.

Finally, update the Gradio wiring in `build_app()`. Change:

```python
        begin_voting_button.click(
            fn=begin_voting,
            inputs=[game_state],
            outputs=[
                begin_voting_button,
                voting_title,
                vote_button_row,
                *candidate_buttons,
                vote_status,
                discussion_status,
            ],
        )

        discussion_status.change(
            fn=lambda status_text: gr.update(visible=bool(status_text)),
            inputs=[discussion_status],
            outputs=[begin_voting_button],
        )

        vote_outputs = [vote_button_row, vote_status, alive_panel, lynched_panel]

        # Both handlers mutate the same shared GameState via cast_votes() and
        # block on AI kickoff calls -- sharing one concurrency slot with
        # cast_player_abstain prevents a double-click (or clicking a
        # candidate and Abstain in quick succession) from racing on that
        # shared state, mirroring the "discussion_turn" guard above.
        for button in candidate_buttons:
            button.click(
                fn=cast_player_vote,
                inputs=[game_state, session_bridge, button],
                outputs=vote_outputs,
                concurrency_limit=1,
                concurrency_id="vote_cast",
            )

        abstain_button.click(
            fn=cast_player_abstain,
            inputs=[game_state, session_bridge],
            outputs=vote_outputs,
            concurrency_limit=1,
            concurrency_id="vote_cast",
        )
```

to:

```python
        begin_voting_button.click(
            fn=begin_voting,
            inputs=[session_bridge, game_state],
            outputs=[
                session_bridge,
                begin_voting_button,
                voting_title,
                vote_button_row,
                *candidate_buttons,
                vote_status,
            ],
            concurrency_limit=None,
        )

        discussion_status.change(
            fn=lambda status_text: gr.update(visible=bool(status_text)),
            inputs=[discussion_status],
            outputs=[begin_voting_button],
        )

        vote_outputs = [vote_button_row, vote_status, alive_panel, lynched_panel]

        # SessionBridge.resolve_input()'s no-pending-future guard (see
        # begin_discussion_button.click above) is what protects a
        # double-click here now -- cast_player_vote/cast_player_abstain no
        # longer call vote-casting logic directly, they resolve the bridge
        # and let VillageFlow.run_voting drive the AI kickoffs.
        for button in candidate_buttons:
            button.click(
                fn=cast_player_vote,
                inputs=[session_bridge, game_state, button],
                outputs=vote_outputs,
                concurrency_limit=None,
            )

        abstain_button.click(
            fn=cast_player_abstain,
            inputs=[session_bridge, game_state],
            outputs=vote_outputs,
            concurrency_limit=None,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ui.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS — every test in `tests/` (including `tests/voting/`, `tests/test_bridge.py`, `tests/test_flow.py`, `tests/discussion/`, `tests/test_night.py`, `tests/test_roster.py`, `tests/test_state.py`, `tests/test_concurrency.py`, `tests/test_ui.py`) is green. This is the first point since Task 2 that the whole suite passes at once.

- [ ] **Step 6: Manual browser pass**

Run `crewai run` (or `uv run app` / however this project normally launches `ui.py` — check `pyproject.toml`'s `[project.scripts]` `app = "the_village.ui:main"` entry) and play a full day through to the vote reveal: Begin Discussion → let it finish → Begin Voting → the ballot appears → cast a vote (or abstain) → "Tallying the votes…" → the reveal shows the breakdown, tally, and outcome, and the alive/lynched panels update. Try it twice to see both a clear lynch and a tie/no-lynch outcome if the random AI votes allow it.

- [ ] **Step 7: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: drive voting through the bridge in ui.py instead of calling it directly"
```
