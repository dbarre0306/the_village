# Day Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `GameState`'s bare `day_number: int` plus four parallel lists (`deaths`, `discussion`, `votes`, `lynchings`) with a `Day` model that owns everything that happened on a given day, so day-number bookkeeping is never duplicated.

**Architecture:** `GameState.days: list[Day]` becomes the single source of truth. `Day` holds `day_number`, `player_killed: str | None`, `discussion: list[DiscussionMessage]`, `votes: list[VoteRecord]`, and `player_lynched: str | None`. `GameState` gains `day_number`/`current_day` read-only properties and an `advance_day()` method so most existing read call sites don't change. `Death` and `Lynching` are removed as classes entirely — a player's name is stored directly. Every call site that touches day-scoped data (`roster.py`, `night.py`, `voting.py`, `discussion/discussion.py`, `village_flow.py`, `ui.py`) and every test that constructs the old shapes is updated to match.

This is a pure in-code refactor — `GameState` is never persisted to disk, so there's no migration format to preserve. Tasks are sequenced foundation-first: Task 1 changes `state.py`, which temporarily breaks every other module until Tasks 2–7 land. Only after Task 7 does the full suite go green again — Task 8 is that final checkpoint.

**Tech Stack:** Python, Pydantic v2 (`BaseModel`), pytest (`pytest-asyncio`, `asyncio_mode = "auto"`), CrewAI (`Flow`).

**Spec:** `docs/superpowers/specs/2026-08-21-day-consolidation-design.md`

## Global Constraints

- Never use YAML config files for CrewAI pieces — jsonc/code only (per `AGENTS.md`); not directly relevant to this refactor, but no task here introduces one.
- No persistence/serialization format is introduced — `GameState` stays in-memory only, exactly as today.
- No behavior changes beyond what the spec calls out (e.g. `DiscussionRunner.run()` now returning only today's transcript is an intentional, spec-approved scope correction, not an accidental behavior change).
- Test runner: `pytest` (configured via `pyproject.toml`: `testpaths = ["tests"]`, `asyncio_mode = "auto"` — async `def test_*` functions need no decorator).

---

## File Structure

No new files are created. Every file below already exists; this plan only modifies them.

- `src/the_village/state.py` — the `Day`/`GameState`/model definitions (Task 1).
- `src/the_village/roster.py` — initial `GameState` construction (Task 2).
- `src/the_village/night.py` — night-one resolution, day advancement (Task 3).
- `src/the_village/voting.py` — vote casting, lynching (Task 4).
- `src/the_village/discussion/discussion.py` — discussion recording/formatting, `DiscussionRunner` (Task 5).
- `src/the_village/village_flow.py` — the top-level `Flow`, setup/night/discussion orchestration (Task 6).
- `src/the_village/bridge.py` — `SessionBridge` outbox type docstring only (Task 6).
- `src/the_village/ui.py` — Gradio panel/transcript formatters (Task 7).
- `tests/test_state.py`, `tests/test_night.py`, `tests/test_voting.py`, `tests/discussion/test_discussion.py`, `tests/test_flow.py`, `tests/test_ui.py` — updated alongside their corresponding source file in the same task.
- `tests/test_roster.py` — **not modified**; it never constructs `GameState` with an explicit `day_number` and reads `state.day_number` only through the property, which is unaffected.

---

### Task 1: `state.py` — the `Day` model and `GameState` rewrite

**Files:**
- Modify: `src/the_village/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces (used by every later task):
  - `class Day(BaseModel)`: `day_number: int`, `player_killed: str | None = None`, `discussion: list[DiscussionMessage] = []`, `votes: list[VoteRecord] = []`, `player_lynched: str | None = None`.
  - `class DiscussionMessage(BaseModel)`: `speaker: str`, `message: str`, `addressed_to: str | None = None` (no `day_number`).
  - `class VoteRecord(BaseModel)`: `voter: str`, `target: str | None = None` (no `day_number`).
  - `class GameState(BaseModel)`: `player_name: str = ""`, `players: list[Player] = []`, `days: list[Day]` (defaults to one `Day(day_number=1)`).
  - `GameState.day_number -> int` (property, last day's number).
  - `GameState.current_day -> Day` (property, last day).
  - `GameState.advance_day(player_killed: str | None = None) -> Day` (appends and returns a new `Day`).
  - `Death` and `Lynching` classes no longer exist — do not import them anywhere after this task.

- [ ] **Step 1: Rewrite the failing test file**

Replace the full contents of `tests/test_state.py`:

```python
from the_village.state import WEEKDAYS, Day, DiscussionMessage, GameState, Player, VoteRecord


def test_player_defaults():
    player = Player(name="Alice", player_type="villager")
    assert player.is_pack_leader is False
    assert player.is_alive is True


def test_weekdays_starts_on_sunday():
    assert WEEKDAYS[0] == "Sunday"
    assert len(WEEKDAYS) == 7


def test_day_defaults():
    day = Day(day_number=1)
    assert day.player_killed is None
    assert day.discussion == []
    assert day.votes == []
    assert day.player_lynched is None


def test_discussion_message_fields():
    message = DiscussionMessage(speaker="Alice", message="hello")
    assert message.speaker == "Alice"
    assert message.message == "hello"
    assert message.addressed_to is None


def test_discussion_message_addressed_to():
    message = DiscussionMessage(
        speaker="Alice",
        message="Bram, where were you?",
        addressed_to="Bram",
    )
    assert message.addressed_to == "Bram"


def test_vote_record_defaults_to_abstain():
    vote = VoteRecord(voter="Alice")
    assert vote.target is None


def test_vote_record_with_target():
    vote = VoteRecord(voter="Alice", target="Bruce")
    assert vote.target == "Bruce"


def test_game_state_defaults():
    state = GameState()
    assert state.player_name == ""
    assert state.players == []
    assert state.days == [Day(day_number=1)]


def test_game_state_day_number_property_reads_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_killed="Bruce")])
    assert state.day_number == 2


def test_game_state_current_day_property_returns_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_killed="Bruce")])
    assert state.current_day == Day(day_number=2, player_killed="Bruce")


def test_advance_day_appends_a_new_day_and_returns_it():
    state = GameState()
    new_day = state.advance_day(player_killed="Bruce")

    assert new_day == Day(day_number=2, player_killed="Bruce")
    assert state.days == [Day(day_number=1), Day(day_number=2, player_killed="Bruce")]
    assert state.current_day == new_day


def test_advance_day_with_no_death():
    state = GameState()
    new_day = state.advance_day()

    assert new_day.player_killed is None
    assert state.day_number == 2
```

- [ ] **Step 2: Run the test file to confirm it fails**

Run: `pytest tests/test_state.py -v`
Expected: FAILs/errors — `Day` doesn't exist yet, `GameState` still has the old fields, `advance_day`/`current_day` don't exist.

- [ ] **Step 3: Rewrite `state.py`**

Replace the full contents of `src/the_village/state.py`:

```python
from typing import Final, Literal

from pydantic import BaseModel, Field

USER: Final = "user"
VILLAGER: Final = "villager"
WEREWOLF: Final = "werewolf"

PlayerType = Literal["user", "villager", "werewolf"]

WEEKDAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]


class Player(BaseModel):
    name: str
    player_type: PlayerType
    is_pack_leader: bool = False
    is_alive: bool = True


class DiscussionMessage(BaseModel):
    speaker: str
    message: str
    addressed_to: str | None = None


class VoteRecord(BaseModel):
    voter: str
    target: str | None = None


class Day(BaseModel):
    day_number: int
    player_killed: str | None = None
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    player_lynched: str | None = None


class GameState(BaseModel):
    player_name: str = ""
    players: list[Player] = []
    days: list[Day] = Field(default_factory=lambda: [Day(day_number=1)])

    def ai_players(self) -> list[str]:
        return filter(lambda player: player.player_type in (VILLAGER, WEREWOLF), self.players)

    @property
    def day_number(self) -> int:
        return self.days[-1].day_number

    @property
    def current_day(self) -> Day:
        return self.days[-1]

    def advance_day(self, player_killed: str | None = None) -> Day:
        new_day = Day(day_number=self.day_number + 1, player_killed=player_killed)
        self.days.append(new_day)
        return new_day
```

- [ ] **Step 4: Run the test file to confirm it passes**

Run: `pytest tests/test_state.py -v`
Expected: PASS (all tests in this file). The rest of the suite is expected to fail/error at this point — every other module still imports `Death`/`Lynching` or constructs the old fields. That's expected until Tasks 2–7 land; don't chase those failures yet.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/state.py tests/test_state.py
git commit -m "refactor: consolidate GameState's day fields into a Day model"
```

---

### Task 2: `roster.py` — drop the redundant `day_number` kwarg

**Files:**
- Modify: `src/the_village/roster.py:42`
- Test: `tests/test_roster.py` (no changes — included here only to confirm it still passes)

**Interfaces:**
- Consumes: `GameState` from Task 1 (the `days` default already yields day 1; no explicit `day_number` kwarg exists anymore).
- Produces: `build_initial_roster(player_name, rng=None) -> GameState` — same signature and behavior as before.

- [ ] **Step 1: Update `roster.py`**

In `src/the_village/roster.py`, change line 42 from:

```python
    return GameState(player_name=player_name, day_number=1, players=players)
```

to:

```python
    return GameState(player_name=player_name, players=players)
```

- [ ] **Step 2: Run the roster tests**

Run: `pytest tests/test_roster.py -v`
Expected: PASS — `test_day_number_starts_at_one` still passes because `GameState.day_number` is a property reading the default `Day(day_number=1)`.

- [ ] **Step 3: Commit**

```bash
git add src/the_village/roster.py
git commit -m "refactor: drop redundant day_number kwarg from build_initial_roster"
```

---

### Task 3: `night.py` — use `advance_day()`

**Files:**
- Modify: `src/the_village/night.py`
- Test: `tests/test_night.py`

**Interfaces:**
- Consumes: `GameState.advance_day(player_killed: str | None = None) -> Day` (Task 1), `GameState.current_day -> Day` (Task 1).
- Produces: `resolve_night_one(state, rng=None) -> GameState` — same signature and behavior as before (kills one eligible villager, advances the day, records who died).

- [ ] **Step 1: Rewrite the failing test file**

Replace the full contents of `tests/test_night.py`:

```python
import random

import pytest

from the_village.night import resolve_night_one
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
    return GameState(player_name="Dana", players=players)


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

- [ ] **Step 2: Run the test file to confirm it fails**

Run: `pytest tests/test_night.py -v`
Expected: FAIL — `night.py` still imports `Death` and manually stamps `state.deaths`/`state.day_number`, which no longer exist as writable fields (`day_number` is a read-only property now, so assigning to it raises `AttributeError` since pydantic properties can't be set without a setter — actually pydantic `BaseModel` disallows setting undeclared attributes by default, so this will raise `ValueError`/`AttributeError` on `state.day_number = new_day`, or fail earlier on `state.deaths.append(...)` with `AttributeError: 'GameState' object has no attribute 'deaths'`).

- [ ] **Step 3: Rewrite `night.py`**

Replace the full contents of `src/the_village/night.py`:

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

- [ ] **Step 4: Run the test file to confirm it passes**

Run: `pytest tests/test_night.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/night.py tests/test_night.py
git commit -m "refactor: resolve_night_one uses GameState.advance_day"
```

---

### Task 4: `voting.py` — vote casting and lynching onto `current_day`

**Files:**
- Modify: `src/the_village/voting.py`
- Test: `tests/test_voting.py`

**Interfaces:**
- Consumes: `GameState.current_day -> Day` (Task 1), `GameState.day_number -> int` (Task 1), `_format_deaths(state) -> str` and `_format_history(state) -> str` from `discussion/discussion.py` (updated in Task 5 — this task only calls them, doesn't change their internals).
- Produces: `_format_lynchings(state) -> str`, `cast_votes(state, agents, player_vote) -> VoteOutcome` — same signatures as before. `VoteOutcome` is unchanged (still has its own `day_number: int` field).

- [ ] **Step 1: Rewrite the failing test file**

Replace the full contents of `tests/test_voting.py`:

```python
from types import SimpleNamespace

from the_village.state import Day, DiscussionMessage, GameState, Player
from the_village.voting import (
    VoteChoice,
    VoteOutcome,
    _build_vote_prompt,
    _format_lynchings,
    _resolve_target,
    cast_votes,
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
        player_name="Dana", days=[Day(day_number=1, player_lynched="C")]
    )
    assert _format_lynchings(state) == "C was lynched by the village on Sunday."


def test_build_vote_prompt_lists_candidates():
    state = GameState(player_name="Dana")
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "A, B" in prompt


def test_build_vote_prompt_includes_full_multi_day_discussion_history():
    state = GameState(
        player_name="Dana",
        days=[
            Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="yesterday's claim")]),
            Day(day_number=2, discussion=[DiscussionMessage(speaker="B", message="today's claim")]),
        ],
    )
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "yesterday's claim" in prompt
    assert "today's claim" in prompt


def test_build_vote_prompt_includes_lynching_history():
    state = GameState(
        player_name="Dana", days=[Day(day_number=1, player_lynched="C")]
    )
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "C was lynched by the village on Sunday." in prompt


class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


def make_voting_state(day_number: int = 2) -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="villager"),
        Player(name="E", player_type="werewolf"),
    ]
    return GameState(
        player_name="Dana", players=players, days=[Day(day_number=day_number)]
    )


def test_majority_vote_lynches_the_top_target():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("B"),
        "B": ScriptedVoteAgent("B"),
        "C": ScriptedVoteAgent("B"),
        "E": ScriptedVoteAgent("B"),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    assert outcome.lynched == "B"
    assert outcome.tally == {"B": 4}
    b = next(v for v in state.players if v.name == "B")
    assert b.is_alive is False
    assert state.current_day.player_lynched == "B"


def test_ai_votes_can_lynch_the_player():
    # The player is a candidate on every AI villager's ballot just like
    # anyone else, so a majority of AI votes against them must be able to
    # flip their is_alive flag -- this is reachable, in-scope behavior even
    # though the day 2+ game loop/win conditions are out of scope.
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("Dana"),
        "B": ScriptedVoteAgent("Dana"),
        "C": ScriptedVoteAgent("Dana"),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote=None)

    assert outcome.lynched == "Dana"
    dana = next(v for v in state.players if v.name == "Dana")
    assert dana.is_alive is False
    assert state.current_day.player_lynched == "Dana"


def test_tie_results_in_no_lynch():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("B"),
        "B": ScriptedVoteAgent("C"),
        "C": ScriptedVoteAgent("A"),
        "E": ScriptedVoteAgent("A"),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    # tally: A=2 (from C, E), B=2 (from Dana, A) -> tied for the top
    assert outcome.lynched is None
    assert state.current_day.player_lynched is None
    assert all(v.is_alive for v in state.players)


def test_all_abstain_results_in_no_lynch():
    state = make_voting_state()
    agents = {name: ScriptedVoteAgent(None) for name in ["A", "B", "C", "E"]}

    outcome = cast_votes(state, agents, player_vote=None)

    assert outcome.lynched is None
    assert outcome.tally == {}


def test_ai_self_vote_is_normalized_to_abstain():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("A"),
        "B": ScriptedVoteAgent(None),
        "C": ScriptedVoteAgent(None),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote=None)

    a_record = next(v for v in outcome.votes if v.voter == "A")
    assert a_record.target is None


def test_dead_villagers_excluded_from_voting_and_targets():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager"),
    ]
    state = GameState(player_name="Dana", players=players, days=[Day(day_number=3)])
    agents = {"B": ScriptedVoteAgent("A")}

    outcome = cast_votes(state, agents, player_vote=None)

    assert "A" not in [record.voter for record in outcome.votes]
    b_record = next(v for v in outcome.votes if v.voter == "B")
    assert b_record.target is None  # A is dead, so an invalid target


def test_player_vote_used_directly_without_kickoff():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent(None),
        "B": ScriptedVoteAgent(None),
        "C": ScriptedVoteAgent(None),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    dana_record = next(v for v in outcome.votes if v.voter == "Dana")
    assert dana_record.target == "B"


def test_votes_recorded_onto_the_current_day():
    state = make_voting_state(day_number=5)
    agents = {name: ScriptedVoteAgent(None) for name in ["A", "B", "C", "E"]}

    outcome = cast_votes(state, agents, player_vote=None)

    assert outcome.day_number == 5
    assert state.current_day.day_number == 5
    assert state.current_day.votes == outcome.votes


def test_prompt_passed_to_agents_includes_full_multi_day_discussion_history():
    state = make_voting_state()
    state.days = [
        Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="yesterday's claim")]),
        Day(day_number=2, discussion=[DiscussionMessage(speaker="B", message="today's claim")]),
    ]
    captured = {}

    class CapturingAgent:
        def kickoff(self, messages, response_format=None):
            captured["prompt"] = messages
            return SimpleNamespace(pydantic=VoteChoice(target=None))

    agents = {
        "A": CapturingAgent(),
        "B": ScriptedVoteAgent(None),
        "C": ScriptedVoteAgent(None),
        "E": ScriptedVoteAgent(None),
    }

    cast_votes(state, agents, player_vote=None)

    assert "yesterday's claim" in captured["prompt"]
    assert "today's claim" in captured["prompt"]
```

- [ ] **Step 2: Run the test file to confirm it fails**

Run: `pytest tests/test_voting.py -v`
Expected: FAIL/ERROR — `voting.py` still imports `Lynching` (removed in Task 1) and reads/writes `state.lynchings`/`state.votes`, which no longer exist.

- [ ] **Step 3: Rewrite `voting.py`**

Replace the full contents of `src/the_village/voting.py`:

```python
from __future__ import annotations

import logging

from crewai import Agent
from pydantic import BaseModel, Field

from the_village.discussion.discussion import (
    _format_deaths,
    _format_history,
    _living_participant_names,
    _weekday,
)
from the_village.state import GameState, VoteRecord

logger = logging.getLogger(__name__)


class VoteChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "The name of the living villager you vote to lynch, or leave "
            "unset to abstain."
        ),
    )


class VoteOutcome(BaseModel):
    day_number: int
    votes: list[VoteRecord]
    tally: dict[str, int]
    lynched: str | None = None


def _resolve_target(
    candidate: str | None, living_names: list[str], exclude: str
) -> str | None:
    if not candidate or candidate == exclude:
        return None
    if candidate not in living_names:
        return None
    return candidate


def _format_lynchings(state: GameState) -> str:
    lynched_days = [day for day in state.days if day.player_lynched]
    if not lynched_days:
        return "(No one has been lynched yet.)"
    return "\n".join(
        f"{day.player_lynched} was lynched by the village on {_weekday(day.day_number)}."
        for day in lynched_days
    )


def _build_vote_prompt(state: GameState, candidates: list[str]) -> str:
    return "\n".join(
        [
            "Known facts:",
            _format_deaths(state),
            _format_lynchings(state),
            "",
            f"Living villagers you may vote to lynch: {', '.join(candidates)}.",
            "",
            "Discussion so far:",
            _format_history(state),
            "",
            "It's time to vote. Decide who you believe is responsible for the "
            "killing and vote to lynch them, or leave your vote unset to "
            "abstain. You may not vote for yourself.",
        ]
    )


def cast_votes(
    state: GameState,
    agents: dict[str, Agent],
    player_vote: str | None,
) -> VoteOutcome:
    living_names = _living_participant_names(state)
    votes: list[VoteRecord] = []

    for name in living_names:
        if name == state.player_name:
            target = _resolve_target(player_vote, living_names, exclude=name)
        else:
            candidates = [n for n in living_names if n != name]
            prompt = _build_vote_prompt(state, candidates)
            output = agents[name].kickoff(prompt, response_format=VoteChoice)
            choice = output.pydantic or VoteChoice()
            target = _resolve_target(choice.target, living_names, exclude=name)
            if choice.target and target is None:
                logger.warning(
                    "Discarding %s's invalid vote for %r", name, choice.target
                )
        votes.append(VoteRecord(voter=name, target=target))

    state.current_day.votes.extend(votes)

    tally: dict[str, int] = {}
    for vote in votes:
        if vote.target is not None:
            tally[vote.target] = tally.get(vote.target, 0) + 1

    lynched: str | None = None
    if tally:
        top_count = max(tally.values())
        top_targets = [name for name, count in tally.items() if count == top_count]
        if len(top_targets) == 1:
            lynched = top_targets[0]

    if lynched is not None:
        player = next(p for p in state.players if p.name == lynched)
        player.is_alive = False
        state.current_day.player_lynched = lynched

    return VoteOutcome(
        day_number=state.day_number, votes=votes, tally=tally, lynched=lynched
    )
```

- [ ] **Step 4: Run the test file to confirm it passes**

Run: `pytest tests/test_voting.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting.py tests/test_voting.py
git commit -m "refactor: cast_votes and lynching read/write GameState.current_day"
```

---

### Task 5: `discussion/discussion.py` — recording and formatting onto `Day`

**Files:**
- Modify: `src/the_village/discussion/discussion.py`
- Test: `tests/discussion/test_discussion.py`

**Interfaces:**
- Consumes: `GameState.current_day -> Day`, `GameState.day_number -> int` (Task 1).
- Produces: `_format_deaths(state) -> str`, `_format_history(state) -> str`, `_record_message(state, speaker, message, addressed_to) -> DiscussionMessage`, `DiscussionRunner.run() -> list[DiscussionMessage]` (now returns **only the current day's** discussion, not every day's — see spec) — every other `DiscussionRunner`/`_run_ai_turn`/`_run_player_turn` signature is unchanged.

- [ ] **Step 1: Rewrite the failing test file**

Replace the full contents of `tests/discussion/test_discussion.py`:

```python
import asyncio
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.discussion.discussion import (
    DECLINED_TO_RESPOND,
    AddressResolution,
    DiscussionRunner,
    TurnOutput,
    _format_deaths,
    _format_history,
    _record_message,
    _resolve_target,
    _run_ai_turn,
    _run_player_turn,
)
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.state import Day, DiscussionMessage, GameState, Player


def test_format_deaths_with_no_deaths():
    state = GameState(player_name="Dana")
    assert _format_deaths(state) == "(No one has died yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(player_name="Dana", days=[Day(day_number=2, player_killed="D")])
    assert _format_deaths(state) == "D was found dead on Monday."


def test_format_history_with_no_messages():
    state = GameState(player_name="Dana")
    assert _format_history(state) == "(No discussion has happened yet.)"


def test_format_history_includes_prior_days_in_order():
    state = GameState(
        player_name="Dana",
        days=[
            Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="yesterday's message")]),
            Day(day_number=2, discussion=[DiscussionMessage(speaker="B", message="today's message")]),
        ],
    )
    assert (
        _format_history(state)
        == "A: yesterday's message\nB: today's message"
    )


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(player_name="Dana", players=players)


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def _stub_agents(names: list[str]) -> dict[str, Agent]:
    return {name: _stub_agent() for name in names}


def test_turn_output_has_no_addressed_to_field():
    assert "addressed_to" not in TurnOutput.model_fields


def test_record_message_appends_to_current_day_and_returns_it():
    state = make_discussion_state()
    msg = _record_message(state, "A", "hello", addressed_to="B")
    assert state.current_day.discussion == [msg]
    assert msg.speaker == "A"
    assert msg.message == "hello"
    assert msg.addressed_to == "B"
    assert state.current_day.day_number == 1


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_discussion_state()
    assert _resolve_target(None, state, exclude="A") is None
    assert _resolve_target("A", state, exclude="A") is None
    assert _resolve_target("Ghost", state, exclude="A") is None
    assert _resolve_target("B", state, exclude="A") == "B"


async def test_run_ai_turn_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(TurnOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message is None
    assert state.current_day.discussion == []


async def test_run_ai_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "B", "Where were you?", addressed_to="A")
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(TurnOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=asking
        )
    assert message.speaker == "A"
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_ai_turn_records_message_and_resolved_address():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                TurnOutput(has_something_to_say=True, message="I saw B leave."),
                AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message.message == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.current_day.discussion == [message]


async def test_run_ai_turn_discards_addressed_to_from_the_analyst_on_decline():
    """Even if the analyst task somehow returns an address for a decline (it
    still runs -- see the spec's resolved decision to keep one uniform
    two-task Crew shape), a decline's recorded message must not carry it."""
    state = make_discussion_state()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                TurnOutput(has_something_to_say=False), AddressResolution(addressed_to="B")
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message is None


async def test_run_player_turn_returns_none_on_scheduled_pass():
    # SessionBridge.resolve_input() is a no-op until wait_for_input() has a
    # pending future (see test_bridge.py), so the task must reach its await
    # point (a sleep(0) yield) before we resolve it -- calling resolve_input
    # first would hang forever.
    state = make_discussion_state()
    bridge = SessionBridge()
    task = asyncio.create_task(
        _run_player_turn(
            analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=None
        )
    )
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(message=None))
    message = await task
    assert message is None
    assert state.current_day.discussion == []


async def test_run_player_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "A", "Where were you?", addressed_to="Dana")
    bridge = SessionBridge()
    task = asyncio.create_task(
        _run_player_turn(
            analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=asking
        )
    )
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(message=None))
    message = await task
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_player_turn_resolves_address_via_the_analyst():
    state = make_discussion_state()
    bridge = SessionBridge()
    with patch(
        "the_village.discussion.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(AddressResolution(addressed_to="B"))),
    ):
        task = asyncio.create_task(
            _run_player_turn(
                analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=None
            )
        )
        await asyncio.sleep(0)
        bridge.resolve_input(PlayerInput(message="B, where were you?"))
        message = await task
    assert message.speaker == "Dana"
    assert message.message == "B, where were you?"
    assert message.addressed_to == "B"


def make_discussion_runner_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="werewolf", is_pack_leader=True),
        Player(name="D", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", players=players)


def _decline_result():
    return _crew_result(TurnOutput(has_something_to_say=False), None)


async def test_discussion_runner_runs_two_rounds_where_everyone_gets_a_turn():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        runner = DiscussionRunner(
            state=make_discussion_runner_state(),
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=random.Random(1),
        )
        transcript = await runner.run()

    # Everyone declines every turn in this test, so no DiscussionMessages are
    # recorded -- what's under test is that the runner runs to completion
    # (doesn't hang) across two full rounds without error.
    assert transcript == []


class NoShuffleRandom:
    """A `random.Random` stand-in whose `shuffle` is a no-op, so a round's
    order is exactly `_living_participant_names`' insertion order -- lets a
    test assert on *which* participant produces which scripted response
    without depending on a real shuffle's output for a given seed."""

    def shuffle(self, _seq):
        pass

    def randrange(self, start, _stop):
        return start


async def test_discussion_runner_resolves_a_bonus_reply_chain():
    bridge = SessionBridge()
    # make_discussion_runner_state()'s villagers list is [Dana, A, B, C, D];
    # with no shuffling, round order is exactly that -- Dana first (the
    # player, auto-passes below), then A, whose scripted response addresses
    # B; B's own scripted decline stops the chain there.
    state = make_discussion_runner_state()

    speak_and_address_b = _crew_result(
        TurnOutput(has_something_to_say=True, message="B, where were you?"),
        AddressResolution(addressed_to="B"),
    )
    responses = iter([speak_and_address_b] + [_decline_result()] * 20)

    async def scripted_akickoff(*_args, **_kwargs):
        return next(responses)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.discussion.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        runner = DiscussionRunner(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=NoShuffleRandom(),
        )
        transcript = await runner.run()

    addressed_messages = [m for m in transcript if m.message == "B, where were you?"]
    assert len(addressed_messages) == 1
    assert addressed_messages[0].speaker == "A"
    assert addressed_messages[0].addressed_to == "B"
    decline_replies = [
        m for m in transcript if m.speaker == "B" and m.message == DECLINED_TO_RESPOND
    ]
    assert len(decline_replies) == 1


async def test_discussion_runner_pauses_for_player_and_resumes():
    bridge = SessionBridge()
    state = make_discussion_runner_state()

    async def scripted_akickoff(*_args, **_kwargs):
        return _decline_result()

    with patch("the_village.discussion.discussion.Crew.akickoff", new=scripted_akickoff):
        runner = DiscussionRunner(
            state=state,
            bridge=bridge,
            player_agents=_stub_agents(["A", "B", "C", "D"]),
            analyst=_stub_agent(),
            rng=random.Random(1),
        )
        task = asyncio.create_task(runner.run())

        # Drain the outbox for the whole run, answering every player-turn
        # pause immediately. The runner asks the player once per round (two
        # rounds total), so more than one pause is expected here -- race
        # each outbox.get() against the runner task itself so a final
        # completion with nothing left in the outbox doesn't leave this
        # loop blocked on a get() that will never resolve.
        seen_waiting_for_turn = False
        while not task.done():
            get_item = asyncio.ensure_future(bridge.outbox.get())
            done, _pending = await asyncio.wait(
                {task, get_item}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_item not in done:
                get_item.cancel()
                break
            item = get_item.result()
            if item == FlowStatus.WAITING_FOR_TURN:
                seen_waiting_for_turn = True
                bridge.resolve_input(PlayerInput(message=None))
        assert seen_waiting_for_turn

        transcript = await task

    assert isinstance(transcript, list)
```

- [ ] **Step 2: Run the test file to confirm it fails**

Run: `pytest tests/discussion/test_discussion.py -v`
Expected: FAIL/ERROR — `discussion.py` still imports `WEEKDAYS` fine, but constructs `DiscussionMessage(day_number=..., ...)` (no longer a valid field) and reads/writes `state.discussion`/`state.deaths`, which no longer exist on `GameState`.

- [ ] **Step 3: Rewrite `discussion/discussion.py`**

In `src/the_village/discussion/discussion.py`, the import line (line 12, `from the_village.state import WEEKDAYS, DiscussionMessage, GameState`) needs no change — `Day` isn't referenced by name in this file, only iterated via `state.days`. Replace these functions (leave everything else — `TurnOutput`, `AddressResolution`, `_resolve_target`, `_living_participant_names`, `_build_speak_prompt`, `_build_analyze_prompt`, `_build_player_analyze_prompt`, `_run_ai_turn`, `_resolve_player_address`, `_run_player_turn`, and the whole `DiscussionRunner` class body below `run()` — unchanged):

Replace `_format_deaths` (lines 57-63):

```python
def _format_deaths(state: GameState) -> str:
    dead_days = [day for day in state.days if day.player_killed]
    if not dead_days:
        return "(No one has died yet.)"
    return "\n".join(
        f"{day.player_killed} was found dead on {_weekday(day.day_number)}."
        for day in dead_days
    )
```

Replace `_format_history` (lines 66-69):

```python
def _format_history(state: GameState) -> str:
    messages = [message for day in state.days for message in day.discussion]
    if not messages:
        return "(No discussion has happened yet.)"
    return "\n".join(f"{m.speaker}: {m.message}" for m in messages)
```

Replace `_record_message` (lines 79-97):

```python
def _record_message(
    state: GameState, speaker: str, message: str, addressed_to: str | None
) -> DiscussionMessage:
    msg = DiscussionMessage(
        speaker=speaker,
        message=message,
        addressed_to=addressed_to,
    )
    state.current_day.discussion.append(msg)
    logger.debug(
        "_record_message: state=%s day=%s speaker=%s addressed_to=%s message=%r",
        id(state),
        state.day_number,
        speaker,
        addressed_to,
        message,
    )
    return msg
```

Replace `DiscussionRunner.run` (lines 240-248):

```python
    async def run(self) -> list[DiscussionMessage]:
        logger.debug(
            "DiscussionRunner.run: runner=%s bridge=%s day=%s",
            id(self),
            id(self.bridge),
            self.state.day_number,
        )
        await self._run_rounds()
        return self.state.current_day.discussion
```

In `_run_round` (around line 272), the debug log reads `message.day_number` — replace that log statement:

```python
        for player in living_players:
            message = await self._give_player_a_turn_to_speak(player, addressed_by=None)
            if message is not None:
                logger.debug(
                    "DiscussionRunner._run_round: runner=%s putting message=%s speaker=%s day=%s",
                    id(self),
                    id(message),
                    message.speaker,
                    self.state.day_number,
                )
                await self.bridge.outbox.put(message)
                await self._resolve_address_chain(message)
```

In `_resolve_address_chain` (around line 348), the debug log reads `reply.day_number` — replace that log statement:

```python
        logger.debug(
            "DiscussionRunner._resolve_address_chain: runner=%s putting reply=%s speaker=%s day=%s",
            id(self),
            id(reply),
            reply.speaker,
            self.state.day_number,
        )
```

- [ ] **Step 4: Run the test file to confirm it passes**

Run: `pytest tests/discussion/test_discussion.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion/discussion.py tests/discussion/test_discussion.py
git commit -m "refactor: discussion recording/formatting reads and writes Day"
```

---

### Task 6: `village_flow.py` + `bridge.py` — flow orchestration

**Files:**
- Modify: `src/the_village/village_flow.py:26-58`
- Modify: `src/the_village/bridge.py:28` (docstring only)
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: `GameState.current_day -> Day`, `DiscussionRunner.run() -> list[DiscussionMessage]` (now current-day-only, from Task 5).
- Produces: `VillageFlow` — same public shape (`setup_game`, `run_night_one`, `announce_death`, `run_discussion` steps) as before. The item `announce_death` puts on `bridge.outbox` is now a bare `str` (the victim's name) instead of a `Death` object.

- [ ] **Step 1: Rewrite the failing test file**

Replace the full contents of `tests/test_flow.py`:

```python
# tests/test_flow.py
import asyncio

from the_village.bridge import PlayerInput, SessionBridge
from the_village.village_flow import VillageFlow


async def test_village_flow_produces_valid_night_one_result_and_pauses_for_discussion():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": "Dana"}))

    # announce_death() puts the victim's name (a bare str) on the outbox as
    # the very first item -- nothing else is queued before it, since
    # run_discussion() (which queues DiscussionMessage/FlowStatus items)
    # only starts after this pause is resolved.
    player_killed = await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state = flow.state
    assert len(state.players) == 7
    assert state.day_number == 2
    assert state.current_day.player_killed == player_killed

    killed = next(v for v in state.players if v.name == player_killed)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.players if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True


async def test_village_flow_builds_player_agents_onto_the_bridge():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": "Dana"}))

    await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    ai_names = {
        v.name for v in flow.state.players if v.player_type in ("villager", "werewolf")
    }
    assert set(bridge.player_agents.keys()) == ai_names
```

- [ ] **Step 2: Run the test file to confirm it fails**

Run: `pytest tests/test_flow.py -v`
Expected: FAIL — `village_flow.py` still assigns `self.state.day_number = roster_state.day_number` (a read-only property now) and reads `self.state.deaths[-1]`/`self.state.discussion`, which no longer exist.

- [ ] **Step 3: Update `village_flow.py`**

In `src/the_village/village_flow.py`, change `setup_game` (lines 26-32):

```python
    @start()
    async def setup_game(self):
        roster_state = build_initial_roster(self.state.player_name)
        self.state.days = roster_state.days
        self.state.players = roster_state.players
        self._player_agents = self._build_ai_agents()
        self._analyst = build_conversation_analyst_agent()
        self.bridge.player_agents = self._player_agents
```

Change `announce_death` (lines 38-41):

```python
    @listen(run_night_one)
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.current_day.player_killed)
        await self.bridge.wait_for_input()
```

Change `run_discussion`'s debug log (lines 43-58) — only the last argument to the second `logger.debug` call changes, from `len(self.state.discussion)` to `len(self.state.current_day.discussion)`:

```python
    @listen(announce_death)
    async def run_discussion(self):
        logger.debug("VillageFlow.run_discussion: flow=%s bridge=%s entering", id(self), id(self.bridge))
        await DiscussionRunner(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
            analyst=self._analyst,
        ).run()
        logger.debug(
            "VillageFlow.run_discussion: flow=%s bridge=%s discussion runner returned, transcript len=%s",
            id(self),
            id(self.bridge),
            len(self.state.current_day.discussion),
        )
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
```

- [ ] **Step 4: Update `bridge.py`'s outbox docstring**

In `src/the_village/bridge.py`, change the `SessionBridge` docstring (lines 26-34) from:

```python
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | Death |
    FlowStatus); `pending_input` carries the one UI -> Flow value a paused
    Flow step is waiting on. Reused for every pause point across the whole
    session (the death-announcement gate, every discussion turn) rather than
    built fresh per pause, so ui.py has one bridge per session to hold in
    `gr.State`.
    """
```

to:

```python
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | str | FlowStatus
    -- the death announcement is a bare str, the victim's name);
    `pending_input` carries the one UI -> Flow value a paused Flow step is
    waiting on. Reused for every pause point across the whole session (the
    death-announcement gate, every discussion turn) rather than built fresh
    per pause, so ui.py has one bridge per session to hold in `gr.State`.
    """
```

- [ ] **Step 5: Run the test file to confirm it passes**

Run: `pytest tests/test_flow.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/the_village/village_flow.py src/the_village/bridge.py tests/test_flow.py
git commit -m "refactor: VillageFlow reads/writes GameState.days, announces death by name"
```

---

### Task 7: `ui.py` — panel and transcript formatters

**Files:**
- Modify: `src/the_village/ui.py:240-277,333-350`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `GameState.days`, `GameState.current_day`, `GameState.day_number` (Task 1); `Day.player_killed`, `Day.player_lynched`, `Day.discussion`, `Day.day_number`.
- Produces: `format_event_log`, `format_deaths_panel`, `format_lynched_panel`, `format_discussion_transcript` — same signatures as before.

- [ ] **Step 1: Rewrite the failing test file**

Replace the full contents of `tests/test_ui.py`:

```python
import asyncio
from types import SimpleNamespace

import gradio as gr
import pytest

from the_village import ui
from the_village.bridge import FlowFailed, FlowStatus, PlayerInput, SessionBridge
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


def make_state_with_one_death() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    return GameState(
        player_name="Dana",
        players=players,
        days=[Day(day_number=2, player_killed="A")],
    )


def test_format_event_log_with_no_deaths():
    state = GameState(player_name="Dana")
    assert format_event_log(state) == "<strong>Nothing has happened yet.</strong>"


def test_format_event_log_with_a_death():
    state = make_state_with_one_death()
    log = format_event_log(state)
    assert (
        log == "<strong>Monday morning: "
        f'<span class="{ui.DEATH_LINE_CLASS}">'
        "A was found dead, torn apart by a werewolf attack.</span></strong>"
    )


def test_format_deaths_panel_with_no_deaths():
    state = GameState(player_name="Dana")
    assert format_deaths_panel(state) == '<div class="chip-list">No one has been killed yet.</div>'


def test_format_deaths_panel_with_a_death():
    state = make_state_with_one_death()
    assert (
        format_deaths_panel(state)
        == '<div class="chip-list"><span class="villager-chip dead" '
        'style="color: var(--speaker-1)">A</span></div>'
    )


def test_format_alive_panel_marks_player_and_excludes_dead_villagers():
    state = make_state_with_one_death()
    assert (
        format_alive_panel(state)
        == '<div class="chip-list"><span class="villager-chip" '
        'style="color: var(--speaker-0)">Dana (me)</span></div>'
    )


def test_format_alive_panel_with_no_villagers():
    state = GameState(player_name="Dana")
    assert format_alive_panel(state) == '<div class="chip-list">No one is left.</div>'


async def test_start_game_rejects_blank_name():
    with pytest.raises(gr.Error):
        async for _ in start_game("   "):
            pass


async def test_start_game_yields_once_paused_at_the_death_gate():
    outputs = [update async for update in start_game("TestPlayer")]

    assert len(outputs) == 1
    bridge = outputs[0][7]
    assert isinstance(bridge, SessionBridge)
    assert bridge.pending_input is not None


async def test_begin_discussion_resolves_the_death_gate_and_streams_to_completion():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [
        update async for update in begin_discussion(bridge, GameState())
    ]

    assert await waiter == PlayerInput()
    assert len(outputs) == 2  # immediate "hide button" yield, then completion


async def test_begin_discussion_is_a_noop_when_already_resolved():
    bridge = SessionBridge()  # nothing pending -- simulates a double-click
    outputs = [update async for update in begin_discussion(bridge, GameState())]
    assert outputs == []


async def test_send_discussion_turn_is_a_noop_on_blank_message():
    bridge = SessionBridge()
    outputs = [update async for update in send_discussion_turn(bridge, GameState(), "   ")]
    assert outputs == [(gr.skip(),) * 7]


async def test_send_discussion_turn_resolves_pending_input():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    async for _ in send_discussion_turn(bridge, GameState(), "I didn't do it!"):
        pass

    assert await waiter == PlayerInput(message="I didn't do it!")


async def test_pass_discussion_turn_resolves_pending_input_with_none():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    async for _ in pass_discussion_turn(bridge, GameState()):
        pass

    assert await waiter == PlayerInput(message=None)


async def test_begin_discussion_raises_gr_error_on_flow_failed():
    # begin_discussion resolves the death-gate future before it starts
    # draining the outbox, so a pending wait must exist first -- this
    # exercises _stream_bridge's FlowFailed handling through its one
    # caller in this test module, rather than reaching into a private name.
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowFailed(detail="boom"))

    with pytest.raises(gr.Error):
        async for _ in begin_discussion(bridge, GameState()):
            pass

    waiter.cancel()


def _discussion_state() -> GameState:
    return GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
    )


async def test_streamed_discussion_message_appears_in_rendered_transcript(monkeypatch):
    # Regression test for the discussion transcript never rendering: the
    # background DiscussionRunner appends messages to this same live
    # GameState (as it does in production -- see the append below), so
    # _stream_bridge must render off of `state.days` as items stream in for
    # the transcript to ever show anything before the whole discussion
    # finishes.
    #
    # Zero out the pacing delay rather than monkeypatching asyncio.sleep
    # itself -- asyncio.sleep is also what the test below uses to yield
    # control to the concurrently-running waiter task, and a stub that
    # doesn't actually suspend (unlike real sleep(0)) breaks that
    # cooperative handoff in ways that are very confusing to debug.
    monkeypatch.setattr(ui, "SPEAKER_THINKING_DELAY_SECONDS", 0)

    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    state = _discussion_state()
    message = DiscussionMessage(speaker="A", message="I saw something strange.")
    # DiscussionRunner._record_message appends to state.current_day.discussion
    # before putting the message on the outbox -- mirror that ordering here.
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    assert any("I saw something strange." in t for t in transcripts)
    assert len(state.current_day.discussion) == 1
    assert state.current_day.discussion[0].message == "I saw something strange."
    waiter.cancel()


async def test_ai_turn_shows_pending_placeholder_before_revealing_message(monkeypatch):
    sleep_calls = []
    real_sleep = asyncio.sleep

    async def spy_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)  # still a real checkpoint, just instant

    monkeypatch.setattr(ui.asyncio, "sleep", spy_sleep)

    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await real_sleep(0)

    state = _discussion_state()
    message = DiscussionMessage(speaker="A", message="hi there")
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [update async for update in begin_discussion(bridge, state)]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    pending_index = next(i for i, t in enumerate(transcripts) if "typing-indicator" in t)
    assert "hi there" not in transcripts[pending_index]
    assert "A:</span>" in transcripts[pending_index]
    assert "hi there" in transcripts[pending_index + 1]
    assert sleep_calls == [ui.SPEAKER_THINKING_DELAY_SECONDS]
    waiter.cancel()


async def test_player_message_shows_immediately_without_placeholder_or_sleep(monkeypatch):
    sleep_calls = []
    real_sleep = asyncio.sleep

    async def spy_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(ui.asyncio, "sleep", spy_sleep)

    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await real_sleep(0)
    # The player's own message is pushed onto the outbox exactly the same
    # way an AI turn is (discussion.py's _run_round doesn't distinguish),
    # so this exercises the same _stream_bridge path with speaker ==
    # state.player_name.
    state = _discussion_state()
    message = DiscussionMessage(speaker="Dana", message="It wasn't me!")
    state.current_day.discussion.append(message)
    await bridge.outbox.put(message)
    await bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)

    outputs = [
        update async for update in send_discussion_turn(bridge, state, "It wasn't me!")
    ]
    transcripts = [update[1] for update in outputs if isinstance(update[1], str)]

    assert not any("typing-indicator" in t for t in transcripts)
    assert any("It wasn't me!" in t for t in transcripts)
    assert sleep_calls == []
    waiter.cancel()


def test_format_discussion_transcript_with_no_messages():
    state = GameState(player_name="Dana")
    assert format_discussion_transcript(state) == ""


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="hello")])],
    )
    transcript = format_discussion_transcript(state)
    assert "A:</span> hello" in transcript
    assert "hello" in transcript


def test_format_discussion_transcript_with_pending_speaker_hides_its_message():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=1, discussion=[DiscussionMessage(speaker="A", message="hello")])],
    )
    transcript = format_discussion_transcript(state, pending_speaker="A")
    assert "hello" not in transcript
    assert "typing-indicator" in transcript
    assert "A:</span>" in transcript


def test_format_discussion_transcript_with_pending_speaker_keeps_prior_messages():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[
            Day(
                day_number=1,
                discussion=[
                    DiscussionMessage(speaker="A", message="first"),
                    DiscussionMessage(speaker="Dana", message="second"),
                ],
            )
        ],
    )
    transcript = format_discussion_transcript(state, pending_speaker="Dana")
    assert "first" in transcript
    assert "second" not in transcript
    assert "typing-indicator" in transcript


def test_format_lynched_panel_with_no_lynchings():
    state = GameState(player_name="Dana")
    assert (
        format_lynched_panel(state)
        == '<div class="chip-list">No one has been lynched yet.</div>'
    )


def test_format_lynched_panel_with_a_lynching():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
    ]
    state = GameState(
        player_name="Dana",
        players=players,
        days=[Day(day_number=2, player_lynched="A")],
    )
    assert (
        format_lynched_panel(state)
        == '<div class="chip-list"><span class="villager-chip dead" '
        'style="color: var(--speaker-1)">A</span></div>'
    )


def test_vote_candidate_names_excludes_player_and_dead():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(player_name="Dana", players=players)
    assert ui._vote_candidate_names(state) == ["A"]


def test_vote_button_updates_labels_living_candidates_and_hides_extra_slots():
    players = [Player(name="Dana", player_type="user")] + [
        Player(name=n, player_type="villager") for n in ["A", "B"]
    ]
    state = GameState(player_name="Dana", players=players)

    updates = ui._vote_button_updates(state)

    assert len(updates) == ui.MAX_VOTE_CANDIDATES
    assert updates[0].value == "A"
    assert updates[0].visible is True
    assert f"speaker-btn-{ui._speaker_color_index('A', state)}" in updates[0].elem_classes
    assert updates[1].value == "B"
    assert updates[1].visible is True
    assert f"speaker-btn-{ui._speaker_color_index('B', state)}" in updates[1].elem_classes
    assert updates[2].visible is False


def test_begin_voting_shows_vote_controls_and_hides_begin_button():
    state = GameState(
        player_name="Dana",
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


def test_build_app_does_not_raise():
    ui.build_app()


class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


def test_colored_name_wraps_name_in_speaker_color_span():
    state = GameState(
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
    )
    assert ui._colored_name("A", state) == '<span style="color: var(--speaker-1)">A</span>'


def test_format_vote_result_lists_breakdown_and_lynch_outcome():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    outcome = VoteOutcome(
        day_number=2,
        votes=[
            VoteRecord(voter="Dana", target="A"),
            VoteRecord(voter="B", target=None),
        ],
        tally={"A": 1},
        lynched="A",
    )
    state = GameState(player_name="Dana", players=players, days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    dana = ui._colored_name("Dana", state)
    a = ui._colored_name("A", state)
    b = ui._colored_name("B", state)
    assert f"{dana} voted for {a}." in result
    assert f"{b} abstained." in result
    assert f"{a}: 1" in result
    assert f"{a} was lynched by the village." in result
    # Dana and A land in different speaker slots, so their spans differ.
    assert dana != a


def test_format_vote_result_omits_tally_line_when_no_non_abstain_votes():
    outcome = VoteOutcome(
        day_number=2,
        votes=[VoteRecord(voter="Dana", target=None)],
        tally={},
        lynched=None,
    )
    state = GameState(player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert f"{ui._colored_name('Dana', state)} abstained." in result
    # The tally separator only ever appears in the tally-counts line, so its
    # absence confirms no (empty) tally line was rendered.
    assert "  ·  " not in result


def test_format_vote_result_reports_tie():
    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1, "B": 1}, lynched=None)
    state = GameState(player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert "tied" in result.lower()


def test_format_vote_result_reports_no_votes():
    outcome = VoteOutcome(day_number=2, votes=[], tally={}, lynched=None)
    state = GameState(player_name="Dana", days=[Day(day_number=2)])

    result = format_vote_result(state, outcome)

    assert "no one voted" in result.lower()


def test_cast_player_vote_hides_controls_before_blocking_call():
    state = GameState(
        player_name="Dana",
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
        player_name="Dana",
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
        player_name="Dana",
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
        player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="A", player_type="villager"),
        ],
        days=[Day(day_number=2)],
    )
    bridge = SessionBridge(player_agents={"A": ScriptedVoteAgent(None)})

    list(cast_player_abstain(state, bridge))

    dana_record = next(v for v in state.current_day.votes if v.voter == "Dana")
    assert dana_record.target is None
```

- [ ] **Step 2: Run the test file to confirm it fails**

Run: `pytest tests/test_ui.py -v`
Expected: FAIL/ERROR — `ui.py` still imports `Death`/`Lynching` and reads `state.deaths`/`state.discussion`/`state.lynchings`, none of which exist anymore.

- [ ] **Step 3: Update `ui.py`**

The import line (line 8, `from the_village.state import WEEKDAYS, DiscussionMessage, GameState`) needs no change — `Day` isn't referenced directly inside `ui.py`, only iterated via `state.days`.

Replace `format_event_log` (lines 240-253):

```python
def format_event_log(state: GameState) -> str:
    dead_days = [day for day in state.days if day.player_killed]
    if not dead_days:
        return "<strong>Nothing has happened yet.</strong>"
    lines = [
        "<strong>"
        f"{WEEKDAYS[(day.day_number - 1) % 7]} morning: "
        f'<span class="{DEATH_LINE_CLASS}">'
        + DEATH_MESSAGE_TEMPLATES[index % len(DEATH_MESSAGE_TEMPLATES)].format(
            name=day.player_killed
        )
        + "</span></strong>"
        for index, day in enumerate(dead_days)
    ]
    return "\n\n".join(lines)
```

Replace `format_deaths_panel` (lines 256-265):

```python
def format_deaths_panel(state: GameState) -> str:
    dead_days = [day for day in state.days if day.player_killed]
    if not dead_days:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been killed yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(day.player_killed, state)})">'
        f"{day.player_killed}</span>"
        for day in dead_days
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'
```

Replace `format_lynched_panel` (lines 268-277):

```python
def format_lynched_panel(state: GameState) -> str:
    lynched_days = [day for day in state.days if day.player_lynched]
    if not lynched_days:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been lynched yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(day.player_lynched, state)})">'
        f"{day.player_lynched}</span>"
        for day in lynched_days
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'
```

Replace `format_discussion_transcript` (lines 333-350):

```python
def format_discussion_transcript(
    state: GameState, pending_speaker: str | None = None
) -> str:
    # `pending_speaker` hides the last message (already appended to
    # state.current_day.discussion by the time this is called) and shows a
    # "typing" placeholder for that speaker instead, so the reveal can be
    # paced.
    all_messages = [message for day in state.days for message in day.discussion]
    messages = all_messages[:-1] if pending_speaker is not None else all_messages
    lines = [
        f"{_speaker_name_span(m.speaker, state)} {m.message}" for m in messages
    ]
    if pending_speaker is not None:
        lines.append(
            f'{_speaker_name_span(pending_speaker, state)} '
            f'<span class="{TYPING_INDICATOR_CLASS}"><span></span><span></span><span></span></span>'
        )
    if not lines:
        return ""
    return "\n\n".join(lines)
```

- [ ] **Step 4: Run the test file to confirm it passes**

Run: `pytest tests/test_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "refactor: ui.py panel/transcript formatters read GameState.days"
```

---

### Task 8: Full-suite verification

**Files:** None modified — verification only.

**Interfaces:** N/A.

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -v`
Expected: PASS — every test across `tests/` (including `tests/test_roster.py`, untouched since Task 2) passes with no failures or errors.

- [ ] **Step 2: Search for any leftover references to the removed names**

Run: `grep -rn "\bDeath\b\|\bLynching\b" src/ tests/`
Expected: no output. If anything matches, it's a leftover from a prior task — fix it and re-run Step 1 before continuing.

Run: `grep -rn "day_number=" src/ tests/ | grep -v "class Day\|day\.day_number\|Day(day_number\|self\.day_number\|state\.day_number\|outcome\.day_number\|new_day = Day(day_number"`
Expected: no output (or only matches you've already reviewed as intentional, e.g. `VoteOutcome(day_number=...)` construction sites, which are expected to remain). This catches any stray `Death(...)`/`Lynching(...)`/`DiscussionMessage(day_number=...)`/`VoteRecord(day_number=...)` construction the rewrite may have missed.

- [ ] **Step 3: Smoke-test the CLI entry point**

Run: `crewai test` (per `AGENTS.md`'s testing convention)
Expected: completes without hanging or raising — `VillageFlow`'s auto-play consumer (`_auto_play_consumer` in `village_flow.py`) still recognizes the death announcement (now a bare `str`) since its dispatch logic (`if item in (...) or not isinstance(item, FlowStatus)`) never depended on `Death` as a type.

This step is a smoke test, not a new automated check — if it fails, treat it as a bug found late and fix the offending file before considering the plan complete.

- [ ] **Step 4: No commit for this task**

This task is verification-only; nothing changes if all steps pass. If Step 1, 2, or 3 turns up an issue, fix it in the relevant file, re-run this task's steps, and commit the fix with a message describing what was missed (e.g. `fix: catch a leftover state.discussion reference in <file>`).

---

## Self-Review

**Spec coverage:**
- `Day` model + `GameState.days`/`day_number`/`current_day`/`advance_day()` → Task 1.
- `Death`/`Lynching` removed, `player_killed`/`player_lynched` as bare strings → Task 1 (model), Tasks 3/4/6/7 (call sites).
- `DiscussionMessage`/`VoteRecord` drop `day_number` → Task 1 (model), Tasks 4/5/6/7 (call sites and tests).
- No `all_discussion()` helper; `_format_history()` and the transcript panel walk `state.days` directly → Task 5, Task 7.
- `DiscussionRunner.run()` returns only the current day's discussion → Task 5.
- `roster.py` drops the explicit `day_number=1` kwarg → Task 2.
- `night.py` uses `advance_day()` → Task 3.
- `voting.py`'s `_format_lynchings`/`cast_votes` → Task 4.
- `village_flow.py`'s `setup_game`/`announce_death`/debug log, `bridge.py`'s docstring → Task 6.
- `ui.py`'s three panel formatters and the transcript formatter → Task 7.
- Test suite fully updated, all call sites covered → Tasks 1–7 (per-file), Task 8 (integration checkpoint).
- Out-of-scope items (`VoteOutcome`, `TurnOutput`, `Player`, discussion/night/voting *logic*, persistence) → untouched by every task above; confirmed no task modifies them beyond the exact lines the spec calls out.

**Placeholder scan:** No task above uses "TBD"/"similar to Task N"/unwritten test bodies — every step includes literal code. `Task 8` intentionally has no code changes (it's a verification task), which is stated explicitly rather than implied.

**Type consistency check:** `Day.player_killed`/`Day.player_lynched` (Task 1) are read the same way in every later task — `day.player_killed`/`day.player_lynched` (Tasks 4, 5, 7) and `state.current_day.player_killed`/`state.current_day.player_lynched` (Tasks 3, 4, 6, 7). `GameState.advance_day(player_killed: str | None = None)` (Task 1) is called with that exact keyword in Task 3. `DiscussionRunner.run()`'s new return value (`self.state.current_day.discussion`, Task 5) matches what Task 6's `run_discussion()` log line and Task 8's verification expect (nothing captures the return value in production code, confirmed in Task 6). No signature drift found.
