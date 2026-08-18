# Lynch Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a voting phase — every living villager (player + AI villagers + AI werewolves) votes to lynch someone or abstains after discussion, votes stay secret until everyone has voted, and the top vote-getter is lynched (a tie or zero votes means no one is lynched) — plus the Gradio UI to drive it.

**Architecture:** A new `voting.py` module (mirroring the shape of `discussion.py`/`night.py`) exposes a single synchronous `cast_votes()` function — no turn-by-turn generator is needed since votes are secret-until-revealed rather than interleaved dialogue. It reuses the exact `Agent` objects `start_discussion()` already built, and two new `GameState` fields (`votes`, `lynchings`) persist the results. `ui.py` gains a "Begin Voting" button, a player ballot (one button per living AI villager plus "Abstain"), and a reveal panel.

**Tech Stack:** Python, Pydantic, CrewAI (`Agent.kickoff`), Gradio, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-lynch-voting-design.md`

## Global Constraints

- No persistence across server restarts — `GameState` lives in-memory per session only.
- Voting is plain Python orchestration (deterministic tallying), not a CrewAI Flow step or Crew process.
- Reuse the exact `Agent` instances `start_discussion()` already built for the day — never rebuild agents for voting.
- Persona/behavior guidance is prompt-level only, never code-enforced (matches the discussion design's precedent).
- A tie for the highest vote count, or zero non-abstain votes, always means no one is lynched — never break ties randomly.
- Every AI vote prompt includes the full multi-day `state.discussion` history (via `discussion.py`'s `_format_history`), not just the current day's messages.
- `Lynching` is a separate model/list from `Death` — never merged, since their UI flavor text differs.

---

### Task 1: `VoteRecord` and `Lynching` state models

**Files:**
- Modify: `src/the_village/state.py:34-42`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `VoteRecord(day_number: int, voter: str, target: str | None = None)`, `Lynching(name: str, day_number: int)`, `GameState.votes: list[VoteRecord]`, `GameState.lynchings: list[Lynching]` — used by every later task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state.py`:

```python
def test_vote_record_defaults_to_abstain():
    vote = VoteRecord(day_number=2, voter="Alice")
    assert vote.target is None


def test_vote_record_with_target():
    vote = VoteRecord(day_number=2, voter="Alice", target="Bruce")
    assert vote.target == "Bruce"


def test_lynching_fields():
    lynching = Lynching(name="Bruce", day_number=2)
    assert lynching.name == "Bruce"
    assert lynching.day_number == 2


def test_game_state_votes_and_lynchings_default_to_empty_list():
    state = GameState()
    assert state.votes == []
    assert state.lynchings == []
```

Update the top import line to:

```python
from the_village.state import WEEKDAYS, Death, DiscussionMessage, GameState, Lynching, VoteRecord, Villager
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'VoteRecord'`

- [ ] **Step 3: Implement the models**

In `src/the_village/state.py`, insert after the `DiscussionMessage` class (currently ending at line 34) and before `class GameState(BaseModel):`:

```python
class VoteRecord(BaseModel):
    day_number: int
    voter: str
    target: str | None = None


class Lynching(BaseModel):
    name: str
    day_number: int
```

Then update `GameState` to add the two new fields after `discussion`:

```python
class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1
    villagers: list[Villager] = []
    deaths: list[Death] = []
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    lynchings: list[Lynching] = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/state.py tests/test_state.py
git commit -m "feat: add VoteRecord and Lynching state models"
```

---

### Task 2: `voting.py` — `VoteChoice`, `VoteOutcome`, `_resolve_target`

**Files:**
- Create: `src/the_village/voting.py`
- Test: `tests/test_voting.py`

**Interfaces:**
- Consumes: `GameState`, `VoteRecord`, `Lynching` from Task 1.
- Produces: `VoteChoice(target: str | None = None)`, `VoteOutcome(day_number: int, votes: list[VoteRecord], tally: dict[str, int], lynched: str | None = None)`, `_resolve_target(candidate: str | None, living_names: list[str], exclude: str) -> str | None` — used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voting.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_voting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.voting'`

- [ ] **Step 3: Implement**

Create `src/the_village/voting.py`:

```python
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from the_village.state import VoteRecord

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voting.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting.py tests/test_voting.py
git commit -m "feat: add vote models and target resolution to voting.py"
```

---

### Task 3: `voting.py` — vote prompt construction

**Files:**
- Modify: `src/the_village/voting.py`
- Test: `tests/test_voting.py`

**Interfaces:**
- Consumes: `discussion._format_deaths(state) -> str`, `discussion._format_history(state) -> str` (both already exist in `src/the_village/discussion.py`, take `GameState` and return `str`, unfiltered by day).
- Produces: `_format_lynchings(state: GameState) -> str`, `_build_vote_prompt(state: GameState, candidates: list[str]) -> str` — used by Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voting.py`:

```python
from the_village.state import DiscussionMessage, GameState, Lynching
from the_village.voting import _build_vote_prompt, _format_lynchings


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_voting.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_vote_prompt'`

- [ ] **Step 3: Implement**

In `src/the_village/voting.py`, add the import and two functions after `_resolve_target`:

```python
from the_village.discussion import _format_deaths, _format_history
from the_village.state import GameState, VoteRecord
```

(Replace the existing `from the_village.state import VoteRecord` line with the two lines above.)

```python
def _format_lynchings(state: GameState) -> str:
    if not state.lynchings:
        return "(No one has been lynched yet.)"
    return "\n".join(
        f"{lynching.name} was lynched by the village on day {lynching.day_number}."
        for lynching in state.lynchings
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voting.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting.py tests/test_voting.py
git commit -m "feat: build vote prompts from full discussion and lynching history"
```

---

### Task 4: `voting.py` — `cast_votes()` resolution

**Files:**
- Modify: `src/the_village/voting.py`
- Test: `tests/test_voting.py`

**Interfaces:**
- Consumes: `discussion._living_participant_names(state: GameState) -> list[str]` (already exists, returns living villagers' names in roster order); `VoteChoice`, `VoteOutcome`, `_resolve_target`, `_build_vote_prompt` from Tasks 2–3; `VoteRecord`, `Lynching` from Task 1.
- Produces: `cast_votes(state: GameState, agents: dict[str, Agent], player_vote: str | None) -> VoteOutcome` — used by Task 8's UI handler.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voting.py`:

```python
from types import SimpleNamespace

from the_village.state import Villager
from the_village.voting import cast_votes


class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


def make_voting_state(day_number: int = 2) -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="E", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=day_number, villagers=villagers)


def test_majority_vote_lynches_the_top_target():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("B"),
        "B": ScriptedVoteAgent("B"),
        "C": ScriptedVoteAgent("B"),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    assert outcome.lynched == "B"
    assert outcome.tally == {"B": 4}
    b = next(v for v in state.villagers if v.name == "B")
    assert b.is_alive is False
    assert state.lynchings == [Lynching(name="B", day_number=2)]


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
    assert state.lynchings == []
    assert all(v.is_alive for v in state.villagers)


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
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager", is_alive=False),
        Villager(name="B", player_type="villager"),
    ]
    state = GameState(player_name="Dana", day_number=3, villagers=villagers)
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


def test_vote_records_stamped_with_current_day_number():
    state = make_voting_state(day_number=5)
    agents = {name: ScriptedVoteAgent(None) for name in ["A", "B", "C", "E"]}

    outcome = cast_votes(state, agents, player_vote=None)

    assert all(record.day_number == 5 for record in outcome.votes)
    assert all(record.day_number == 5 for record in state.votes)


def test_prompt_passed_to_agents_includes_full_multi_day_discussion_history():
    state = make_voting_state()
    state.discussion = [
        DiscussionMessage(day_number=1, speaker="A", message="yesterday's claim"),
        DiscussionMessage(day_number=2, speaker="B", message="today's claim"),
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

Add `DiscussionMessage` to the `from the_village.state import ...` line at the top of `tests/test_voting.py` if it isn't already imported from Task 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_voting.py -v`
Expected: FAIL with `ImportError: cannot import name 'cast_votes'`

- [ ] **Step 3: Implement**

In `src/the_village/voting.py`, update the imports at the top:

```python
from crewai import Agent

from the_village.discussion import (
    _format_deaths,
    _format_history,
    _living_participant_names,
)
from the_village.state import GameState, Lynching, VoteRecord
```

Add `cast_votes` at the end of the file:

```python
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
        votes.append(
            VoteRecord(day_number=state.day_number, voter=name, target=target)
        )

    state.votes.extend(votes)

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
        villager = next(v for v in state.villagers if v.name == lynched)
        villager.is_alive = False
        state.lynchings.append(Lynching(name=lynched, day_number=state.day_number))

    return VoteOutcome(
        day_number=state.day_number, votes=votes, tally=tally, lynched=lynched
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voting.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/voting.py tests/test_voting.py
git commit -m "feat: resolve votes into a lynch outcome in cast_votes"
```

---

### Task 5: `ui.py` — `format_lynched_panel`

**Files:**
- Modify: `src/the_village/ui.py:233-242`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `GameState.lynchings: list[Lynching]` from Task 1; existing `CHIP_LIST_CLASS`, `VILLAGER_CHIP_CLASS`, `_speaker_color_index(name, state)`.
- Produces: `format_lynched_panel(state: GameState) -> str` — used by Task 7 (initial render) and Task 8 (post-vote update).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
from the_village.state import Lynching
from the_village.ui import format_lynched_panel


def test_format_lynched_panel_with_no_lynchings():
    state = GameState(player_name="Dana", day_number=1)
    assert (
        format_lynched_panel(state)
        == '<div class="chip-list">No one has been lynched yet.</div>'
    )


def test_format_lynched_panel_with_a_lynching():
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager", is_alive=False),
    ]
    state = GameState(
        player_name="Dana",
        day_number=2,
        villagers=villagers,
        lynchings=[Lynching(name="A", day_number=2)],
    )
    assert (
        format_lynched_panel(state)
        == '<div class="chip-list"><span class="villager-chip dead" '
        'style="color: var(--speaker-1)">A</span></div>'
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_lynched_panel'`

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`, add after `format_deaths_panel` (which currently ends at line 242):

```python
def format_lynched_panel(state: GameState) -> str:
    if not state.lynchings:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been lynched yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(lynching.name, state)})">'
        f"{lynching.name}</span>"
        for lynching in state.lynchings
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: add lynched-villagers panel formatting"
```

---

### Task 6: `ui.py` — vote candidate button helpers

**Files:**
- Modify: `src/the_village/ui.py:30-34`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `GameState.villagers`, `GameState.player_name`.
- Produces: `MAX_VOTE_CANDIDATES = 6`, `_vote_candidate_names(state: GameState) -> list[str]`, `_vote_button_updates(state: GameState) -> list[dict]` (each a `gr.update(value=..., visible=...)`, length `MAX_VOTE_CANDIDATES`) — used by Task 7's `begin_voting`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
def test_vote_candidate_names_excludes_player_and_dead():
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(player_name="Dana", villagers=villagers)
    assert ui._vote_candidate_names(state) == ["A"]


def test_vote_button_updates_labels_living_candidates_and_hides_extra_slots():
    villagers = [Villager(name="Dana", player_type="user")] + [
        Villager(name=n, player_type="villager") for n in ["A", "B"]
    ]
    state = GameState(player_name="Dana", villagers=villagers)

    updates = ui._vote_button_updates(state)

    assert len(updates) == ui.MAX_VOTE_CANDIDATES
    assert updates[0]["value"] == "A"
    assert updates[0]["visible"] is True
    assert updates[1]["value"] == "B"
    assert updates[1]["visible"] is True
    assert updates[2]["visible"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v`
Expected: FAIL with `AttributeError: module 'the_village.ui' has no attribute '_vote_candidate_names'`

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`, add the constant after `DEATH_LINE_CLASS = "death-line"` (currently line 34):

```python
# Matches roster.py's fixed count of 6 sampled AI villagers -- the vote
# ballot pre-allocates this many button slots since Gradio's layout is
# fixed at build time and can't grow/shrink with who's still alive.
MAX_VOTE_CANDIDATES = 6
```

Add the two helper functions after `format_lynched_panel` (added in Task 5):

```python
def _vote_candidate_names(state: GameState) -> list[str]:
    return [
        villager.name
        for villager in state.villagers
        if villager.is_alive and villager.name != state.player_name
    ]


def _vote_button_updates(state: GameState) -> list:
    names = _vote_candidate_names(state)
    updates = []
    for i in range(MAX_VOTE_CANDIDATES):
        if i < len(names):
            updates.append(gr.update(value=names[i], visible=True))
        else:
            updates.append(gr.update(visible=False))
    return updates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: compute vote ballot button labels from living villagers"
```

---

### Task 7: `ui.py` — Begin Voting button and vote screen layout

**Files:**
- Modify: `src/the_village/ui.py` (layout inside `build_app()`, `start_game()`, new `begin_voting` handler)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `_vote_button_updates` from Task 6; `format_lynched_panel` from Task 5.
- Produces: `begin_voting(state: GameState) -> tuple` (10 elements: begin_voting_button update, vote_button_row update, `*` 6 candidate button updates, vote_status update, discussion_status update); new Gradio components `begin_voting_button`, `vote_button_row`, `candidate_buttons` (list of 6), `abstain_button`, `vote_status`, `lynched_panel`, all wired into `build_app()`. Task 8 wires `candidate_buttons`/`abstain_button` clicks and consumes `vote_button_row`/`vote_status`/`lynched_panel` as outputs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
def test_begin_voting_shows_vote_controls_and_hides_begin_button():
    state = GameState(
        player_name="Dana",
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )

    outputs = ui.begin_voting(state)

    (
        begin_button_update,
        row_update,
        *candidate_updates,
        status_update,
        discussion_status_update,
    ) = outputs
    assert begin_button_update["visible"] is False
    assert row_update["visible"] is True
    assert len(candidate_updates) == ui.MAX_VOTE_CANDIDATES
    assert candidate_updates[0]["value"] == "A"
    assert candidate_updates[0]["visible"] is True
    assert status_update["visible"] is False
    assert discussion_status_update["visible"] is False


def test_build_app_does_not_raise():
    ui.build_app()
```

Also update the existing test asserting `start_game`'s output count, since Task 7 adds `lynched_panel` to its return tuple:

```python
def test_start_game_returns_seven_outputs_including_game_state():
    outputs = start_game("TestPlayer")

    assert len(outputs) == 7
    assert "TestPlayer" not in outputs[2]
    assert "TestPlayer" not in outputs[3]
    assert "TestPlayer (me)" in outputs[4]
    assert isinstance(outputs[6], GameState)
    assert outputs[6].player_name == "TestPlayer"
```

Delete the old `test_start_game_returns_six_outputs_including_game_state` test it replaces.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v`
Expected: FAIL — `test_start_game_returns_seven_outputs_including_game_state` fails on `len(outputs) == 7` (currently 6), and `test_begin_voting_shows_vote_controls_and_hides_begin_button` fails with `AttributeError: module 'the_village.ui' has no attribute 'begin_voting'`.

- [ ] **Step 3: Implement**

Add the `begin_voting` handler in `src/the_village/ui.py`, after `pass_discussion_turn` (currently ending at line 401):

```python
def begin_voting(state: GameState):
    return (
        gr.update(visible=False),  # begin_voting_button
        gr.update(visible=True),  # vote_button_row
        *_vote_button_updates(state),
        gr.update(visible=False),  # vote_status
        gr.update(visible=False),  # discussion_status
    )
```

Update `start_game` to also format the new lynched panel. Change:

```python
    state = flow.state
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
        format_alive_panel(state),
        state,
    )
```

to:

```python
    state = flow.state
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
        format_alive_panel(state),
        format_lynched_panel(state),
        state,
    )
```

In `build_app()`, add a third column to the pinned bar. Change:

```python
            with gr.Row(elem_classes=[PINNED_BAR_CLASS]):
                with gr.Column():
                    gr.Markdown("### Living Villagers")
                    alive_panel = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Killed by Werewolves")
                    deaths_panel = gr.Markdown()
```

to:

```python
            with gr.Row(elem_classes=[PINNED_BAR_CLASS]):
                with gr.Column():
                    gr.Markdown("### Living Villagers")
                    alive_panel = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Killed by Werewolves")
                    deaths_panel = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Lynched by the Village")
                    lynched_panel = gr.Markdown()
```

Immediately after the existing `discussion_status = gr.Markdown(visible=False)` line (inside the same `with gr.Column():` block — do not duplicate that line, only add what follows it):

```python
                begin_voting_button = gr.Button(
                    "Begin Voting",
                    elem_classes=[BEGIN_DISCUSSION_BUTTON_CLASS],
                    visible=False,
                )
                with gr.Row(visible=False) as vote_button_row:
                    candidate_buttons = [
                        gr.Button(visible=False) for _ in range(MAX_VOTE_CANDIDATES)
                    ]
                    abstain_button = gr.Button("Abstain")
                vote_status = gr.Markdown(visible=False)
```

Update the `start_button.click(...)` outputs list to include `lynched_panel`:

```python
        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[
                start_screen,
                result_screen,
                event_log,
                deaths_panel,
                alive_panel,
                lynched_panel,
                game_state,
            ],
            concurrency_limit=None,
        )
```

Add the click wiring at the end of `build_app()`, just before `return demo`:

```python
        begin_voting_button.click(
            fn=begin_voting,
            inputs=[game_state],
            outputs=[
                begin_voting_button,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: add Begin Voting button and vote ballot layout"
```

---

### Task 8: `ui.py` — casting the player's vote and revealing the outcome

**Files:**
- Modify: `src/the_village/ui.py` (new handlers, wiring in `build_app()`)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `cast_votes(state, agents, player_vote) -> VoteOutcome` from Task 4; `format_lynched_panel`, `format_alive_panel`, `_vote_candidate_names`/`_vote_button_updates`/`candidate_buttons`/`abstain_button`/`vote_button_row`/`vote_status` from Tasks 5–7.
- Produces: `format_vote_result(state: GameState, outcome: VoteOutcome) -> str`, `cast_player_vote(state, runner, target) -> Iterator[tuple]` (yields `(vote_button_row_update, vote_status_update, alive_panel_value, lynched_panel_value)`), `cast_player_abstain(state, runner) -> Iterator[tuple]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
from the_village.discussion import DiscussionRunner
from the_village.state import VoteRecord
from the_village.ui import cast_player_abstain, cast_player_vote, format_vote_result
from the_village.voting import VoteChoice, VoteOutcome


class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


def test_format_vote_result_lists_breakdown_and_lynch_outcome():
    outcome = VoteOutcome(
        day_number=2,
        votes=[
            VoteRecord(day_number=2, voter="Dana", target="A"),
            VoteRecord(day_number=2, voter="B", target=None),
        ],
        tally={"A": 1},
        lynched="A",
    )
    state = GameState(player_name="Dana", day_number=2)

    result = format_vote_result(state, outcome)

    assert "Dana voted for A." in result
    assert "B abstained." in result
    assert "A was lynched by the village." in result


def test_format_vote_result_reports_tie():
    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1, "B": 1}, lynched=None)
    state = GameState(player_name="Dana", day_number=2)

    result = format_vote_result(state, outcome)

    assert "tied" in result.lower()


def test_format_vote_result_reports_no_votes():
    outcome = VoteOutcome(day_number=2, votes=[], tally={}, lynched=None)
    state = GameState(player_name="Dana", day_number=2)

    result = format_vote_result(state, outcome)

    assert "no one voted" in result.lower()


def test_cast_player_vote_hides_controls_before_blocking_call():
    state = GameState(
        player_name="Dana",
        day_number=2,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state, agents={"A": ScriptedVoteAgent(None)}, budgets={}
    )

    events = cast_player_vote(state, runner, "A")
    first_event = next(events)

    row_update, status_update, _, _ = first_event
    assert row_update["visible"] is False
    assert status_update["value"] == "Tallying the votes…"


def test_cast_player_vote_reveals_outcome_and_updates_panels():
    state = GameState(
        player_name="Dana",
        day_number=2,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state, agents={"A": ScriptedVoteAgent(None)}, budgets={}
    )

    events = list(cast_player_vote(state, runner, "A"))
    _, status_update, alive_panel_value, lynched_panel_value = events[-1]

    assert "A was lynched by the village." in status_update["value"]
    assert "Dana" in alive_panel_value
    assert "A" not in alive_panel_value
    assert "A" in lynched_panel_value


def test_cast_player_vote_wraps_unexpected_errors_as_gr_error():
    state = GameState(
        player_name="Dana",
        day_number=2,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )

    class BoomAgent:
        def kickoff(self, *args, **kwargs):
            raise RuntimeError("boom")

    runner = DiscussionRunner(state=state, agents={"A": BoomAgent()}, budgets={})

    with pytest.raises(gr.Error):
        list(cast_player_vote(state, runner, "A"))


def test_cast_player_abstain_records_no_target():
    state = GameState(
        player_name="Dana",
        day_number=2,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state, agents={"A": ScriptedVoteAgent(None)}, budgets={}
    )

    list(cast_player_abstain(state, runner))

    dana_record = next(v for v in state.votes if v.voter == "Dana")
    assert dana_record.target is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v`
Expected: FAIL with `ImportError: cannot import name 'cast_player_vote'`

- [ ] **Step 3: Implement**

Add the import at the top of `src/the_village/ui.py`:

```python
from the_village.voting import VoteOutcome, cast_votes
```

Add these functions after `begin_voting` (added in Task 7):

```python
def format_vote_result(state: GameState, outcome: VoteOutcome) -> str:
    lines = [
        f"{record.voter} voted for {record.target}."
        if record.target is not None
        else f"{record.voter} abstained."
        for record in outcome.votes
    ]
    lines.append("")
    if outcome.lynched is not None:
        lines.append(f"**{outcome.lynched} was lynched by the village.**")
    elif outcome.tally:
        lines.append("**The vote was tied — no one was lynched.**")
    else:
        lines.append("**No one voted to lynch anyone — no one was lynched.**")
    return "\n\n".join(lines)


def cast_player_vote(state: GameState, runner: DiscussionRunner, target: str | None):
    # Hide the ballot the instant the player votes, before the blocking AI
    # kickoff calls run -- the row shouldn't linger visible while they resolve.
    yield (
        gr.update(visible=False),
        gr.update(value="Tallying the votes…", visible=True),
        gr.update(),
        gr.update(),
    )
    try:
        outcome = cast_votes(state, runner.agents, player_vote=target)
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Vote casting failed")
        raise gr.Error("Something went wrong, please try again.") from exc
    yield (
        gr.update(visible=False),
        gr.update(value=format_vote_result(state, outcome), visible=True),
        format_alive_panel(state),
        format_lynched_panel(state),
    )


def cast_player_abstain(state: GameState, runner: DiscussionRunner):
    yield from cast_player_vote(state, runner, None)
```

Add the import for `DiscussionRunner` type hint if not already present at the top of `ui.py` (it already is, via the existing `from the_village.discussion import ... DiscussionRunner, advance, start_discussion` line).

Wire the buttons in `build_app()`, just before `return demo`:

```python
        vote_outputs = [vote_button_row, vote_status, alive_panel, lynched_panel]

        for button in candidate_buttons:
            button.click(
                fn=cast_player_vote,
                inputs=[game_state, discussion_runner_state, button],
                outputs=vote_outputs,
            )

        abstain_button.click(
            fn=cast_player_abstain,
            inputs=[game_state, discussion_runner_state],
            outputs=vote_outputs,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: cast the player's vote and reveal the lynch outcome"
```

---

### Task 9: Full test suite and manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite**

Run: `uv run pytest -v`
Expected: PASS, no failures, no warnings about unused imports.

- [ ] **Step 2: Manual browser verification — clear lynch outcome**

Run: `uv run app`

In the browser:
1. Start a game, click "Begin Discussion", and either send a message or click "I have nothing to say" repeatedly until the discussion completes.
2. Click "Begin Voting" and confirm a ballot appears: one button per living AI villager plus "Abstain".
3. Click a villager's name. Confirm the ballot disappears, a brief "Tallying the votes…" message shows, then the full breakdown (who voted for whom, including abstentions) and a lynch/tie/no-votes outcome line appear.
4. If someone was lynched, confirm the "Living Villagers" panel no longer lists them and the new "Lynched by the Village" panel now does.

- [ ] **Step 3: Manual browser verification — tie outcome**

Since the AI villagers' votes aren't controllable from the UI, replay step 2 across a few fresh games (or days, if a tie doesn't occur on the first try) until a tie is observed. Confirm the outcome line reads "The vote was tied — no one was lynched." and no villager's status changes.

- [ ] **Step 4: Commit** (only if manual verification surfaced fixes)

```bash
git add -A
git commit -m "fix: address issues found during voting manual verification"
```
