# Day-One Discussion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the night-one death, let the player click "Begin Discussion" and have an interactive, turn-based conversation with the 6 surviving AI villagers (including the 2 secret werewolves) about who might be responsible — with the player able to speak, ask/answer questions, be accused, and pass — rendered incrementally in the Gradio UI, with the full transcript preserved on `GameState` for future days to reference.

**Architecture:** A new `discussion.py` module holds plain-Python turn orchestration (budgets, round order, question/accusation interrupts, stop condition) plus one real CrewAI `Agent` per living AI villager, invoked directly via `agent.kickoff(prompt, response_format=TurnOutput)` (no `Task`/`Crew`/`Flow` step — see spec's "Why plain Python orchestration" section). `advance()` is a generator that auto-plays AI turns and yields each message as it's produced, pausing only when the player must act. `ui.py`'s Gradio click handlers are themselves generators that consume `advance()`'s yields and update the transcript incrementally. Discussion messages are appended directly onto `GameState.discussion`, not held in separate throwaway state, so they persist for the life of the game session and are available to future days' agents.

**Tech Stack:** Python 3.13, `crewai==1.15.16` (`Agent.kickoff()` standalone execution, no `Task`/`Crew`), Pydantic (via crewai), `gradio`, `pytest`, `uv` for dependency management.

**Spec:** `docs/superpowers/specs/2026-08-15-day-one-discussion-design.md`

## Global Constraints

- Every participant (6 living AI villagers + the player) starts each discussion with a budget of 3 initiating turns.
- `addressed_to` covers both direct questions and accusations naming another participant — treated identically.
- A bonus reply (triggered by `addressed_to`) costs no budget and never triggers a further chained bonus reply — a reply's own `addressed_to` is recorded but not acted on immediately.
- Passing on a normal rotation turn is **permanent**; passing when answering a direct question/accusation is **not** permanent.
- The participant who spoke most recently today must not be placed first in the next round's order (unless they're the only active participant left).
- Discussion history lives on `GameState.discussion` (in-memory for the session, same durability `GameState` already has — no database, no server-restart survival) so future days' agents can see prior days' messages.
- No real LLM/network calls in unit tests — `Agent.kickoff()` is always replaced with a test double.
- `crewai>=1.15.16,<2.0.0` is already a project dependency; no new dependency is needed for this feature.

---

## File Structure

- Modify `src/the_village/state.py` — add `DiscussionMessage` model and `GameState.discussion` field.
- Create `src/the_village/discussion.py` — all turn orchestration: `DiscussionRunner`, `TurnOutput`, `AdvanceStatus`, `start_discussion()`, `advance()`.
- Modify `src/the_village/ui.py` — new "Begin Discussion" flow: transcript panel, player input row, click wiring.
- Create `tests/test_discussion.py` — orchestration unit tests (mocked agents).
- Modify `tests/test_state.py` — new model tests.
- Modify `tests/test_ui.py` — new UI wiring tests.

---

### Task 1: Discussion data model on `GameState`

**Files:**
- Modify: `src/the_village/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `DiscussionMessage(BaseModel)` with fields `day_number: int`, `speaker: str`, `message: str`, `addressed_to: str | None = None`; `GameState.discussion: list[DiscussionMessage] = []`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_state.py` (add `DiscussionMessage` to the existing import line at the top):

```python
from the_village.state import WEEKDAYS, Death, DiscussionMessage, GameState, Villager
```

Append these tests to the file:

```python
def test_discussion_message_fields():
    message = DiscussionMessage(day_number=1, speaker="Alice", message="hello")
    assert message.day_number == 1
    assert message.speaker == "Alice"
    assert message.message == "hello"
    assert message.addressed_to is None


def test_discussion_message_addressed_to():
    message = DiscussionMessage(
        day_number=1,
        speaker="Alice",
        message="Bram, where were you?",
        addressed_to="Bram",
    )
    assert message.addressed_to == "Bram"


def test_game_state_discussion_defaults_to_empty_list():
    state = GameState()
    assert state.discussion == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'DiscussionMessage'`

- [ ] **Step 3: Implement the model**

In `src/the_village/state.py`, add the new model after `Death` and before `GameState`:

```python
class DiscussionMessage(BaseModel):
    day_number: int
    speaker: str
    message: str
    addressed_to: str | None = None
```

Update `GameState` to add the `discussion` field:

```python
class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1
    villagers: list[Villager] = []
    deaths: list[Death] = []
    discussion: list[DiscussionMessage] = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/state.py tests/test_state.py
git commit -m "feat: add DiscussionMessage model and GameState.discussion field"
```

---

### Task 2: `DiscussionRunner` and `start_discussion()`

**Files:**
- Create: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: `DiscussionMessage`, `GameState`, `Villager` from `the_village.state` (Task 1).
- Produces: `INITIAL_BUDGET: int`; `DiscussionRunner` dataclass with fields `state: GameState`, `agents: dict[str, Agent]`, `budgets: dict[str, int]`, `passed: set[str]`, `rng: random.Random`, `queue: list[str]`, `awaiting_reply_from: str | None`; `start_discussion(state: GameState, rng: random.Random | None = None) -> DiscussionRunner`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_discussion.py`:

```python
import random

from the_village.discussion import DiscussionRunner, start_discussion
from the_village.state import GameState, Villager


def make_state(day_number: int = 2) -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="D", player_type="villager", is_alive=False),
        Villager(name="E", player_type="werewolf", is_pack_leader=True),
        Villager(name="F", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=day_number, villagers=villagers)


def test_start_discussion_builds_agent_for_each_living_ai_villager():
    runner = start_discussion(make_state(), random.Random(1))
    assert set(runner.agents.keys()) == {"A", "B", "C", "E", "F"}


def test_start_discussion_excludes_dead_villager():
    runner = start_discussion(make_state(), random.Random(1))
    assert "D" not in runner.agents
    assert "D" not in runner.budgets


def test_start_discussion_budgets_include_player_and_all_living_ai():
    runner = start_discussion(make_state(), random.Random(1))
    assert runner.budgets == {"Dana": 3, "A": 3, "B": 3, "C": 3, "E": 3, "F": 3}


def test_start_discussion_starts_with_empty_queue_and_no_pending_reply():
    runner = start_discussion(make_state(), random.Random(1))
    assert runner.queue == []
    assert runner.awaiting_reply_from is None
    assert runner.passed == set()


def test_start_discussion_returns_discussion_runner_referencing_the_state():
    state = make_state()
    runner = start_discussion(state, random.Random(1))
    assert isinstance(runner, DiscussionRunner)
    assert runner.state is state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.discussion'`

- [ ] **Step 3: Implement `discussion.py`**

Create `src/the_village/discussion.py`:

```python
from __future__ import annotations

import random
from dataclasses import dataclass, field

from crewai import Agent

from the_village.state import GameState, Villager

INITIAL_BUDGET = 3


@dataclass
class DiscussionRunner:
    state: GameState
    agents: dict[str, Agent]
    budgets: dict[str, int]
    passed: set[str] = field(default_factory=set)
    rng: random.Random = field(default_factory=random.Random)
    queue: list[str] = field(default_factory=list)
    awaiting_reply_from: str | None = None


def _build_agent(villager: Villager, all_villagers: list[Villager]) -> Agent:
    if villager.player_type == "werewolf":
        packmate = next(
            v.name
            for v in all_villagers
            if v.player_type == "werewolf" and v.name != villager.name
        )
        role_knowledge = (
            f"You are secretly a werewolf. Your fellow werewolf is {packmate} — "
            "you know this, no one else does. You want to deflect suspicion "
            "without revealing yourself."
        )
    else:
        role_knowledge = (
            "You are an ordinary villager. You do not know who the werewolves are."
        )
    backstory = (
        f"You are {villager.name}, a resident of a small village playing a game "
        f"of suspicion and survival after a neighbor was found dead. {role_knowledge} "
        "You react like a real person would — with shock, grief, anger, or "
        "suspicion as the moment calls for. You never invent facts, alibis, or "
        "claims that aren't grounded in what you actually know or what has "
        "already been said."
    )
    return Agent(
        role=f"Villager {villager.name}",
        goal=(
            "Discuss the recent death honestly from your own perspective, "
            "without revealing secrets you wouldn't reveal."
        ),
        backstory=backstory,
    )


def start_discussion(
    state: GameState, rng: random.Random | None = None
) -> DiscussionRunner:
    living_ai = [
        v
        for v in state.villagers
        if v.is_alive and v.player_type in ("villager", "werewolf")
    ]
    agents = {v.name: _build_agent(v, state.villagers) for v in living_ai}
    participants = [state.player_name] + [v.name for v in living_ai]
    budgets = {name: INITIAL_BUDGET for name in participants}
    return DiscussionRunner(
        state=state, agents=agents, budgets=budgets, rng=rng or random.Random()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: add DiscussionRunner and start_discussion"
```

---

### Task 3: Round builder (turn order + round-boundary constraint)

**Files:**
- Modify: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: `DiscussionRunner`, `start_discussion` (Task 2); `DiscussionMessage` (Task 1).
- Produces: `_active_participants(runner: DiscussionRunner) -> list[str]`; `_last_speaker_today(runner: DiscussionRunner) -> str | None`; `_build_round(runner: DiscussionRunner) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discussion.py` (add `DiscussionMessage` to the import from `the_village.state`, and add `_active_participants, _build_round, _last_speaker_today` to the import from `the_village.discussion`):

```python
from the_village.discussion import (
    DiscussionRunner,
    _active_participants,
    _build_round,
    _last_speaker_today,
    start_discussion,
)
from the_village.state import DiscussionMessage, GameState, Villager


def make_runner(day_number: int = 1) -> DiscussionRunner:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="E", player_type="werewolf"),
    ]
    state = GameState(player_name="Dana", day_number=day_number, villagers=villagers)
    return start_discussion(state, random.Random(1))


def test_active_participants_excludes_passed():
    runner = make_runner()
    runner.passed.add("A")
    assert "A" not in _active_participants(runner)
    assert "B" in _active_participants(runner)


def test_active_participants_excludes_exhausted_budget():
    runner = make_runner()
    runner.budgets["B"] = 0
    assert "B" not in _active_participants(runner)


def test_last_speaker_today_returns_none_with_no_messages():
    runner = make_runner()
    assert _last_speaker_today(runner) is None


def test_last_speaker_today_ignores_other_days():
    runner = make_runner(day_number=2)
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="yesterday")
    )
    assert _last_speaker_today(runner) is None


def test_last_speaker_today_returns_most_recent_todays_speaker():
    runner = make_runner()
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="first")
    )
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="B", message="second")
    )
    assert _last_speaker_today(runner) == "B"


def test_build_round_includes_only_active_participants():
    runner = make_runner()
    runner.passed.add("A")
    order = _build_round(runner)
    assert set(order) == {"Dana", "B", "C", "E"}


def test_build_round_never_starts_with_previous_last_speaker():
    runner = make_runner()
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="C", message="I saw something")
    )
    for seed in range(200):
        runner.rng = random.Random(seed)
        order = _build_round(runner)
        assert order[0] != "C"


def test_build_round_allows_repeat_when_only_one_active_participant():
    runner = make_runner()
    for name in ("Dana", "B", "C", "E"):
        runner.passed.add(name)
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="only one left")
    )
    order = _build_round(runner)
    assert order == ["A"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: FAIL with `ImportError: cannot import name '_active_participants'`

- [ ] **Step 3: Implement the round builder**

Append to `src/the_village/discussion.py`:

```python
def _active_participants(runner: DiscussionRunner) -> list[str]:
    return [
        name
        for name, budget in runner.budgets.items()
        if budget > 0 and name not in runner.passed
    ]


def _last_speaker_today(runner: DiscussionRunner) -> str | None:
    today_messages = [
        m for m in runner.state.discussion if m.day_number == runner.state.day_number
    ]
    return today_messages[-1].speaker if today_messages else None


def _build_round(runner: DiscussionRunner) -> list[str]:
    order = _active_participants(runner)
    runner.rng.shuffle(order)
    last_speaker = _last_speaker_today(runner)
    if last_speaker is not None and len(order) > 1 and order[0] == last_speaker:
        swap_index = runner.rng.randrange(1, len(order))
        order[0], order[swap_index] = order[swap_index], order[0]
    return order
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: add discussion round builder with speaker rotation rule"
```

---

### Task 4: Target resolution (`addressed_to` validation)

**Files:**
- Modify: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: `DiscussionRunner`, `start_discussion` (Task 2).
- Produces: `_resolve_target(candidate: str | None, runner: DiscussionRunner, exclude: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discussion.py` (add `_resolve_target` to the discussion import):

```python
def test_resolve_target_returns_none_for_none_candidate():
    runner = make_runner()
    assert _resolve_target(None, runner, exclude="A") is None


def test_resolve_target_returns_none_for_self_address():
    runner = make_runner()
    assert _resolve_target("A", runner, exclude="A") is None


def test_resolve_target_returns_none_for_unknown_name():
    runner = make_runner()
    assert _resolve_target("Ghost", runner, exclude="A") is None


def test_resolve_target_returns_none_for_passed_participant():
    runner = make_runner()
    runner.passed.add("B")
    assert _resolve_target("B", runner, exclude="A") is None


def test_resolve_target_returns_valid_target():
    runner = make_runner()
    assert _resolve_target("B", runner, exclude="A") == "B"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_target'`

- [ ] **Step 3: Implement `_resolve_target`**

Append to `src/the_village/discussion.py`:

```python
def _resolve_target(
    candidate: str | None, runner: DiscussionRunner, exclude: str
) -> str | None:
    if not candidate or candidate == exclude:
        return None
    if candidate not in runner.budgets:
        return None
    if candidate in runner.passed:
        return None
    return candidate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: validate addressed_to targets against live participants"
```

---

### Task 5: Prompt building and agent turn execution

**Files:**
- Modify: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: `DiscussionRunner`, `_resolve_target` (Task 4); `DiscussionMessage`, `Death`, `GameState` (Task 1).
- Produces: `TurnOutput(BaseModel)` with fields `has_something_to_say: bool`, `message: str | None = None`, `addressed_to: str | None = None`; `_format_deaths(state: GameState) -> str`; `_format_history(state: GameState) -> str`; `_build_prompt(state: GameState, addressed_by: DiscussionMessage | None) -> str`; `_ask_agent(agent: Agent, state: GameState, addressed_by: DiscussionMessage | None) -> TurnOutput`; `_generate_bonus_reply(runner: DiscussionRunner, msg: DiscussionMessage) -> DiscussionMessage | None`.

Any object with a `.kickoff(messages, response_format=None) -> object with a .pydantic attribute` method satisfies `_ask_agent`'s use of `agent` — tests use a lightweight double (`ScriptedAgent` below) instead of a real `crewai.Agent`, so no network/LLM calls happen in the test suite.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discussion.py` (add `Death` to the `the_village.state` import, and add `TurnOutput, _build_prompt, _format_deaths, _format_history, _generate_bonus_reply` to the discussion import; add `from types import SimpleNamespace` near the top):

```python
from types import SimpleNamespace

from the_village.state import Death


class ScriptedAgent:
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=self._outputs.pop(0))


def test_format_deaths_with_no_deaths():
    state = GameState(player_name="Dana")
    assert _format_deaths(state) == "(No one has died yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(player_name="Dana", deaths=[Death(name="D", day_number=2)])
    assert _format_deaths(state) == "D was found dead on day 2."


def test_format_history_with_no_messages():
    state = GameState(player_name="Dana")
    assert _format_history(state) == "(No discussion has happened yet.)"


def test_format_history_includes_prior_days_in_order():
    state = GameState(
        player_name="Dana",
        discussion=[
            DiscussionMessage(day_number=1, speaker="A", message="yesterday's message"),
            DiscussionMessage(day_number=2, speaker="B", message="today's message"),
        ],
    )
    assert (
        _format_history(state)
        == "A: yesterday's message\nB: today's message"
    )


def test_build_prompt_without_addressed_by_prompts_free_turn():
    state = GameState(player_name="Dana")
    prompt = _build_prompt(state, addressed_by=None)
    assert "It's your turn" in prompt


def test_build_prompt_with_addressed_by_includes_the_question():
    state = GameState(player_name="Dana")
    msg = DiscussionMessage(day_number=1, speaker="A", message="Where were you?")
    prompt = _build_prompt(state, addressed_by=msg)
    assert "A just said to you" in prompt
    assert "Where were you?" in prompt


def test_generate_bonus_reply_records_message_and_costs_no_budget():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [TurnOutput(has_something_to_say=True, message="I was home.")]
    )
    original_budget = runner.budgets["B"]
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply.speaker == "B"
    assert reply.message == "I was home."
    assert runner.budgets["B"] == original_budget
    assert runner.state.discussion[-1] == reply


def test_generate_bonus_reply_returns_none_when_agent_declines():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent([TurnOutput(has_something_to_say=False)])
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply is None
    assert runner.state.discussion == []


def test_generate_bonus_reply_does_not_chain_further_bonus_replies():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [
            TurnOutput(
                has_something_to_say=True,
                message="What about you, A?",
                addressed_to="A",
            )
        ]
    )
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply.addressed_to == "A"
    assert len(runner.state.discussion) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: FAIL with `ImportError: cannot import name 'TurnOutput'`

- [ ] **Step 3: Implement prompt building and turn execution**

Add `from pydantic import BaseModel` and `from the_village.state import DiscussionMessage, GameState, Villager` (extend the existing state import) to the top of `src/the_village/discussion.py`. Then append:

```python
class TurnOutput(BaseModel):
    has_something_to_say: bool
    message: str | None = None
    addressed_to: str | None = None


def _format_deaths(state: GameState) -> str:
    if not state.deaths:
        return "(No one has died yet.)"
    return "\n".join(
        f"{death.name} was found dead on day {death.day_number}."
        for death in state.deaths
    )


def _format_history(state: GameState) -> str:
    if not state.discussion:
        return "(No discussion has happened yet.)"
    return "\n".join(f"{m.speaker}: {m.message}" for m in state.discussion)


def _build_prompt(state: GameState, addressed_by: DiscussionMessage | None) -> str:
    parts = [
        "Known facts:",
        _format_deaths(state),
        "",
        "Discussion so far:",
        _format_history(state),
        "",
    ]
    if addressed_by is not None:
        parts.append(
            f'{addressed_by.speaker} just said to you: "{addressed_by.message}" '
            "Respond directly to this."
        )
    else:
        parts.append(
            "It's your turn. Decide whether you have something to say — a "
            "statement, question, or accusation — or nothing more to add right now."
        )
    return "\n".join(parts)


def _ask_agent(
    agent: Agent, state: GameState, addressed_by: DiscussionMessage | None
) -> TurnOutput:
    prompt = _build_prompt(state, addressed_by)
    output = agent.kickoff(prompt, response_format=TurnOutput)
    return output.pydantic


def _generate_bonus_reply(
    runner: DiscussionRunner, msg: DiscussionMessage
) -> DiscussionMessage | None:
    agent = runner.agents[msg.addressed_to]
    output = _ask_agent(agent, runner.state, addressed_by=msg)
    if not output.has_something_to_say or not output.message:
        return None
    reply = DiscussionMessage(
        day_number=runner.state.day_number,
        speaker=msg.addressed_to,
        message=output.message,
        addressed_to=_resolve_target(
            output.addressed_to, runner, exclude=msg.addressed_to
        ),
    )
    runner.state.discussion.append(reply)
    return reply
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: add discussion prompt building and bonus-reply generation"
```

---

### Task 6: `advance()` — the full turn-taking state machine

**Files:**
- Modify: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: everything from Tasks 2-5 (`DiscussionRunner`, `_build_round`, `_resolve_target`, `_ask_agent`, `_generate_bonus_reply`, `TurnOutput`).
- Produces: `AdvanceStatus` (`Enum` with members `WAITING_FOR_TURN`, `WAITING_FOR_ANSWER`, `COMPLETE`); `advance(runner: DiscussionRunner, player_input: str | None = None, player_addressed_to: str | None = None, player_pass: bool = False) -> Iterator[DiscussionMessage | AdvanceStatus]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discussion.py` (add `Iterator` isn't needed in tests; add `AdvanceStatus, advance` to the discussion import):

```python
def test_advance_auto_plays_ai_turns_then_pauses_for_player():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [TurnOutput(has_something_to_say=True, message="I'm scared.")]
    )
    runner.agents["B"] = ScriptedAgent(
        [TurnOutput(has_something_to_say=True, message="Me too.")]
    )
    runner.queue = ["A", "B", "Dana"]

    events = list(advance(runner))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["A", "B"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN
    assert runner.queue == ["Dana"]


def test_advance_records_player_message_and_decrements_budget():
    runner = make_runner()
    runner.queue = ["Dana"]
    starting_budget = runner.budgets["Dana"]

    events = list(advance(runner, player_input="I didn't do it!"))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert messages[0].speaker == "Dana"
    assert messages[0].message == "I didn't do it!"
    assert runner.budgets["Dana"] == starting_budget - 1


def test_advance_player_pass_on_normal_turn_is_permanent():
    runner = make_runner()
    runner.queue = ["Dana"]

    list(advance(runner, player_pass=True))

    assert "Dana" in runner.passed


def test_advance_pauses_for_player_when_addressed_by_ai():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            TurnOutput(
                has_something_to_say=True,
                message="Dana, where were you last night?",
                addressed_to="Dana",
            )
        ]
    )
    runner.queue = ["A", "B"]

    events = list(advance(runner))

    assert events[-1] == AdvanceStatus.WAITING_FOR_ANSWER
    assert runner.awaiting_reply_from == "Dana"
    assert runner.queue == ["B"]


def test_advance_player_answer_to_question_costs_no_budget_and_resumes_queue():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [TurnOutput(has_something_to_say=True, message="I agree with Dana.")]
    )
    runner.awaiting_reply_from = "Dana"
    runner.queue = ["B"]
    starting_budget = runner.budgets["Dana"]

    events = list(advance(runner, player_input="I was home asleep."))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert messages[0].speaker == "Dana"
    assert messages[0].message == "I was home asleep."
    assert runner.budgets["Dana"] == starting_budget
    assert runner.awaiting_reply_from is None
    assert messages[1].speaker == "B"


def test_advance_player_pass_when_addressed_is_not_permanent():
    runner = make_runner()
    runner.awaiting_reply_from = "Dana"
    runner.queue = []

    list(advance(runner, player_pass=True))

    assert "Dana" not in runner.passed


def test_advance_completes_when_no_participants_remain_active():
    runner = make_runner()
    runner.budgets = {"Dana": 0, "A": 0, "B": 0, "C": 0, "E": 0}
    runner.queue = []

    events = list(advance(runner))

    assert events == [AdvanceStatus.COMPLETE]


def test_advance_ai_addressing_another_ai_does_not_pause_for_player():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            TurnOutput(
                has_something_to_say=True,
                message="B, explain yourself.",
                addressed_to="B",
            )
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [TurnOutput(has_something_to_say=True, message="I have nothing to hide.")]
    )
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["A", "B"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN


def test_advance_ai_pass_marks_participant_permanently_passed():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent([TurnOutput(has_something_to_say=False)])
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    assert [e for e in events if isinstance(e, DiscussionMessage)] == []
    assert "A" in runner.passed
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: FAIL with `ImportError: cannot import name 'AdvanceStatus'`

- [ ] **Step 3: Implement `advance()`**

Add `from enum import Enum` and `from typing import Iterator` to the top of `src/the_village/discussion.py`. Then append:

```python
class AdvanceStatus(str, Enum):
    WAITING_FOR_TURN = "waiting_for_turn"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    COMPLETE = "complete"


def advance(
    runner: DiscussionRunner,
    player_input: str | None = None,
    player_addressed_to: str | None = None,
    player_pass: bool = False,
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    state = runner.state
    player = state.player_name

    if runner.awaiting_reply_from == player:
        runner.awaiting_reply_from = None
        if player_input and not player_pass:
            msg = DiscussionMessage(
                day_number=state.day_number,
                speaker=player,
                message=player_input,
                addressed_to=_resolve_target(
                    player_addressed_to, runner, exclude=player
                ),
            )
            state.discussion.append(msg)
            yield msg

    elif runner.queue and runner.queue[0] == player:
        runner.queue.pop(0)
        if player_pass or not player_input:
            runner.passed.add(player)
        else:
            runner.budgets[player] -= 1
            addressed_to = _resolve_target(
                player_addressed_to, runner, exclude=player
            )
            msg = DiscussionMessage(
                day_number=state.day_number,
                speaker=player,
                message=player_input,
                addressed_to=addressed_to,
            )
            state.discussion.append(msg)
            yield msg
            if addressed_to is not None:
                bonus = _generate_bonus_reply(runner, msg)
                if bonus is not None:
                    yield bonus

    yield from _run_ai_turns(runner)


def _run_ai_turns(
    runner: DiscussionRunner,
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    state = runner.state
    player = state.player_name

    while True:
        if not runner.queue:
            runner.queue = _build_round(runner)
            if not runner.queue:
                yield AdvanceStatus.COMPLETE
                return

        next_name = runner.queue[0]
        if next_name == player:
            yield AdvanceStatus.WAITING_FOR_TURN
            return

        runner.queue.pop(0)
        output = _ask_agent(runner.agents[next_name], state, addressed_by=None)
        if not output.has_something_to_say or not output.message:
            runner.passed.add(next_name)
            continue

        runner.budgets[next_name] -= 1
        addressed_to = _resolve_target(output.addressed_to, runner, exclude=next_name)
        msg = DiscussionMessage(
            day_number=state.day_number,
            speaker=next_name,
            message=output.message,
            addressed_to=addressed_to,
        )
        state.discussion.append(msg)
        yield msg

        if addressed_to == player:
            runner.awaiting_reply_from = player
            yield AdvanceStatus.WAITING_FOR_ANSWER
            return
        elif addressed_to is not None:
            bonus = _generate_bonus_reply(runner, msg)
            if bonus is not None:
                yield bonus
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS

- [ ] **Step 5: Run the full discussion test file once more for a clean read**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS (all discussion tests from Tasks 2-6)

- [ ] **Step 6: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: add advance() turn-taking state machine for discussion"
```

---

### Task 7: Gradio UI — Begin Discussion, transcript, player input

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `DiscussionRunner`, `TurnOutput`, `AdvanceStatus`, `start_discussion`, `advance` from `the_village.discussion` (Tasks 2-6); `DiscussionMessage`, `GameState`, `Villager` from `the_village.state`.
- Produces: `format_discussion_transcript(state: GameState) -> str`; `_living_ai_names(state: GameState) -> list[str]`; `begin_discussion(state: GameState)`; `send_discussion_turn(runner: DiscussionRunner, message: str, addressed_to: str)`; `pass_discussion_turn(runner: DiscussionRunner)` — all three are generators yielding 5-tuples `(DiscussionRunner, str, dict, dict, dict)` matching Gradio's `gr.update()` return shape, and raise `gr.Error` (not a raw exception) if anything fails mid-generator, same pattern `start_game` uses; modifies `start_game(player_name: str)` to return a 6-tuple (adds the `GameState` as the last element).

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_start_game_returns_five_outputs_and_never_targets_the_player` test and extend the imports in `tests/test_ui.py`:

```python
import gradio as gr
import pytest

from the_village.discussion import DiscussionRunner
from the_village.state import DiscussionMessage, Death, GameState, Villager
from the_village.ui import (
    _living_ai_names,
    begin_discussion,
    format_alive_panel,
    format_deaths_panel,
    format_discussion_transcript,
    format_event_log,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
)
```

Replace the old five-output test with:

```python
def test_start_game_returns_six_outputs_including_game_state():
    outputs = start_game("TestPlayer")

    assert len(outputs) == 6
    assert "TestPlayer" not in outputs[2]
    assert "TestPlayer" not in outputs[3]
    assert "TestPlayer (me)" in outputs[4]
    assert isinstance(outputs[5], GameState)
    assert outputs[5].player_name == "TestPlayer"
```

Append these new tests:

```python
def test_format_discussion_transcript_with_no_messages():
    state = GameState(player_name="Dana")
    assert format_discussion_transcript(state) == "The discussion hasn't started yet."


def test_format_discussion_transcript_lists_messages():
    state = GameState(
        player_name="Dana",
        discussion=[DiscussionMessage(day_number=1, speaker="A", message="hello")],
    )
    assert format_discussion_transcript(state) == "**A:** hello"


def test_living_ai_names_excludes_player_and_dead_villagers():
    state = GameState(
        player_name="Dana",
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
            Villager(name="D", player_type="villager", is_alive=False),
            Villager(name="E", player_type="werewolf"),
        ],
    )
    assert _living_ai_names(state) == ["A", "E"]


def test_begin_discussion_yields_waiting_status_when_only_player_active():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )

    events = list(begin_discussion(state))

    runner, transcript, dropdown_update, input_visibility, status_update = events[-1]
    assert isinstance(runner, DiscussionRunner)
    assert input_visibility["visible"] is True
    assert dropdown_update["choices"] == []


def test_send_discussion_turn_records_message_and_shows_waiting_status():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    runner = DiscussionRunner(state=state, agents={}, budgets={"Dana": 3}, queue=["Dana"])

    events = list(send_discussion_turn(runner, "I'm scared.", ""))

    _, transcript, _, input_visibility, _ = events[-1]
    assert "I'm scared." in transcript
    assert input_visibility["visible"] is True


def test_pass_discussion_turn_marks_player_passed_and_shows_ended_status():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[Villager(name="Dana", player_type="user")],
    )
    runner = DiscussionRunner(state=state, agents={}, budgets={"Dana": 3}, queue=["Dana"])

    events = list(pass_discussion_turn(runner))

    _, _, _, input_visibility, status_update = events[-1]
    assert "Dana" in runner.passed
    assert input_visibility["visible"] is False
    assert status_update["visible"] is True


def test_send_discussion_turn_wraps_unexpected_errors_as_gr_error():
    state = GameState(
        player_name="Dana",
        day_number=1,
        villagers=[
            Villager(name="Dana", player_type="user"),
            Villager(name="A", player_type="villager"),
        ],
    )
    runner = DiscussionRunner(
        state=state, agents={}, budgets={"Dana": 3, "A": 3}, queue=["A"]
    )

    with pytest.raises(gr.Error):
        list(send_discussion_turn(runner, "hello", ""))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_discussion_transcript'`

- [ ] **Step 3: Implement the UI changes**

In `src/the_village/ui.py`, add to the imports:

```python
from the_village.discussion import AdvanceStatus, DiscussionRunner, advance, start_discussion
```

Add these functions after `format_alive_panel` and before `start_game`:

```python
def format_discussion_transcript(state: GameState) -> str:
    if not state.discussion:
        return "The discussion hasn't started yet."
    lines = [f"**{m.speaker}:** {m.message}" for m in state.discussion]
    return "\n\n".join(lines)


def _living_ai_names(state: GameState) -> list[str]:
    return [
        villager.name
        for villager in state.villagers
        if villager.is_alive and villager.player_type in ("villager", "werewolf")
    ]


def _drive_discussion(runner, events, dropdown_update=None):
    dropdown_update = dropdown_update if dropdown_update is not None else gr.update()
    try:
        for event in events:
            transcript = format_discussion_transcript(runner.state)
            if isinstance(event, AdvanceStatus):
                complete = event == AdvanceStatus.COMPLETE
                yield (
                    runner,
                    transcript,
                    dropdown_update,
                    gr.update(visible=not complete),
                    gr.update(
                        visible=complete,
                        value="The discussion has ended." if complete else "",
                    ),
                )
            else:
                yield (runner, transcript, dropdown_update, gr.update(), gr.update())
            dropdown_update = gr.update()
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


def begin_discussion(state: GameState):
    runner = start_discussion(state)
    yield from _drive_discussion(
        runner,
        advance(runner),
        dropdown_update=gr.update(choices=_living_ai_names(state)),
    )


def send_discussion_turn(runner: DiscussionRunner, message: str, addressed_to: str):
    events = advance(
        runner,
        player_input=message.strip() or None,
        player_addressed_to=addressed_to or None,
    )
    yield from _drive_discussion(runner, events)


def pass_discussion_turn(runner: DiscussionRunner):
    yield from _drive_discussion(runner, advance(runner, player_pass=True))
```

Modify `start_game` to also return the resulting `GameState`:

```python
def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    try:
        flow = VillageFlow()
        flow.kickoff(inputs={"player_name": player_name.strip()})
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Flow kickoff failed")
        raise gr.Error("Something went wrong, please try again.") from exc

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

Replace `build_app` with a version that adds the discussion UI:

```python
def build_app() -> gr.Blocks:
    with gr.Blocks(title="The Village") as demo:
        game_state = gr.State()
        discussion_runner_state = gr.State()

        with gr.Column(visible=True) as start_screen:
            name_input = gr.Textbox(label="Your first name")
            start_button = gr.Button("Start Game")

        with gr.Row(visible=False) as result_screen:
            with gr.Column():
                gr.Markdown("### Events")
                event_log = gr.Markdown()
                begin_discussion_button = gr.Button("Begin Discussion")
                discussion_transcript = gr.Markdown()
                with gr.Row(visible=False) as discussion_input_row:
                    discussion_textbox = gr.Textbox(label="Say something", scale=3)
                    discussion_addressed_to = gr.Dropdown(
                        label="Address to (optional)", choices=[], scale=1
                    )
                    send_button = gr.Button("Send")
                    pass_button = gr.Button("Pass")
                discussion_status = gr.Markdown(visible=False)
            with gr.Column():
                gr.Markdown("### Alive Villagers")
                alive_panel = gr.Markdown()
                gr.Markdown("### Killed by Werewolves")
                deaths_panel = gr.Markdown()

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[
                start_screen,
                result_screen,
                event_log,
                deaths_panel,
                alive_panel,
                game_state,
            ],
            concurrency_limit=None,
        )

        discussion_outputs = [
            discussion_runner_state,
            discussion_transcript,
            discussion_addressed_to,
            discussion_input_row,
            discussion_status,
        ]

        begin_discussion_button.click(
            fn=begin_discussion,
            inputs=[game_state],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        send_button.click(
            fn=send_discussion_turn,
            inputs=[discussion_runner_state, discussion_textbox, discussion_addressed_to],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        pass_button.click(
            fn=pass_discussion_turn,
            inputs=[discussion_runner_state],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

    return demo
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS (all tests from Tasks 1-7)

- [ ] **Step 6: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: wire Begin Discussion flow into the Gradio UI"
```

---

### Task 8: Manual browser verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm an LLM API key is configured**

This task exercises real `Agent.kickoff()` calls (unlike every automated test in this plan, which mocks them). Confirm `OPENAI_API_KEY` (or whichever provider this project's `.env` is configured for) is set before launching, or the AI villagers' turns will fail.

- [ ] **Step 2: Launch the app**

Run: `uv run app`

In a browser, go to the printed local URL (typically `http://127.0.0.1:7860`):

1. Enter a name (e.g. "Dana") and click "Start Game" — confirm the night-one result screen appears as before.
2. Click "Begin Discussion" — confirm AI villager messages start appearing in the transcript panel one at a time (not all at once), and that a player input row eventually appears (either as a normal turn or because you were addressed).
3. Type a message and click "Send" — confirm it appears in the transcript attributed to your name, and that AI turns resume afterward.
4. Use the "Address to" dropdown to name a specific villager, send a message, and confirm that villager's reply appears immediately after yours, out of the normal rotation.
5. Click "Pass" on a normal turn — confirm no further normal turns are offered to you afterward (you may still be addressed and asked to respond).
6. Let the discussion run to completion (or pass every time you're prompted) — confirm the input row disappears and "The discussion has ended." is shown.
7. Confirm villager dialogue reads like a person reacting to a death (shock, suspicion, etc.), not a flat recitation, and doesn't reference facts absent from the transcript.

Stop the server (Ctrl+C) once verified.

- [ ] **Step 3: Final full-suite check**

Run: `uv run pytest -v`
Expected: PASS
