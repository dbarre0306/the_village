# Discussion Crews + Flow Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `discussion.py`'s hand-rolled `DiscussionRunner`/`advance()` orchestration with a real CrewAI `Crew`-per-utterance mechanism driven by a new `DiscussionFlow`, and make `VillageFlow` the async top-level orchestrator for the whole setup → night → discussion session, so multiple players can run concurrently.

**Architecture:** Each utterance (scheduled turn or bonus reply) is a `Crew` — an AI speaker pairs with the shared Conversation Analyst across two sequential tasks (produce a message, then determine who it addresses); the player's turns skip straight to the analyst since their message already exists. `DiscussionFlow` walks two fixed rounds of every living participant, resolving bonus-reply chains as it goes. `VillageFlow` becomes the async, session-long orchestrator: setup → night → death announcement (paused for a Gradio click) → `DiscussionFlow`. A `SessionBridge` (one `asyncio.Queue` out, one `asyncio.Future` in) is the sole channel between any of this and Gradio, reused for every pause point.

**Tech Stack:** Python 3.10–3.13, CrewAI (`crewai[tools]>=1.15.16,<2.0.0`), Gradio `>=6.24.0`, pytest, `pytest-asyncio` (new dev dependency), `uv`.

**Spec:** `docs/superpowers/specs/2026-08-19-discussion-crew-flow-redesign-design.md`

## Global Constraints

- Every `Crew` turn call uses `await crew.akickoff(...)` — never `crew.kickoff()` or `crew.kickoff_async()` (the latter is `asyncio.to_thread`-wrapped and bottlenecks concurrent players; verified against CrewAI's source and docs in the spec).
- Every `VillageFlow`/`DiscussionFlow` step method is `async def` (async steps are awaited directly on the event loop; sync steps get dispatched to a thread pool — verified against `crewai/flow/runtime/__init__.py` in the spec).
- `SessionBridge` (and any live `asyncio` object) is never passed as a `gr.State()` default value — Gradio requires state defaults to be deep-copyable, and `asyncio.Queue`/`Future`/`Task` aren't. Always build it inside a handler and return it as an output.
- No `concurrency_id`/`concurrency_limit` on the discussion/night event listeners — that pool is global across all users, not per-session (confirmed via Gradio's docs). Use `bridge.resolve_input()`'s built-in no-op-when-nothing-pending behavior for double-click protection instead.
- Voting (`voting.py`) is untouched — same `cast_votes(state, agents, player_vote)` signature, same call sites in `ui.py`, still outside any `Flow`.
- `Crew`/`Agent`/LLM calls are mocked in all unit tests — assertions target orchestration (round structure, addressing, pause/resume), never generated text quality.

---

## Task 1: `SessionBridge` and async test support

**Files:**
- Create: `src/the_village/bridge.py`
- Test: `tests/test_bridge.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `FlowStatus` (`Enum`: `WAITING_FOR_TURN`, `WAITING_FOR_ANSWER`, `DISCUSSION_COMPLETE`), `PlayerInput` (`dataclass`, field `message: str | None = None`), `FlowFailed` (`dataclass`, field `detail: str`), `SessionBridge` (`dataclass`, fields `outbox: asyncio.Queue`, `pending_input: asyncio.Future[PlayerInput] | None`, `task: asyncio.Task | None`, `agents: dict[str, Agent]`; methods `async def wait_for_input(self) -> PlayerInput` and `def resolve_input(self, player_input: PlayerInput) -> bool`), `async def run_flow(coro, bridge: SessionBridge) -> None` (runs `coro`, catching any exception and pushing a `FlowFailed` onto `bridge.outbox` instead of letting it vanish into an unawaited background task).

- [ ] **Step 1: Add `pytest-asyncio` and enable auto mode**

```bash
uv add --dev pytest-asyncio
```

Edit `pyproject.toml`'s `[tool.pytest.ini_options]` to:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`asyncio_mode = "auto"` lets `async def test_...` functions run directly, no `@pytest.mark.asyncio` needed on every one — matches this codebase's existing preference for minimal test boilerplate.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_bridge.py
import asyncio

from the_village.bridge import PlayerInput, SessionBridge


def test_resolve_input_is_a_noop_with_no_pending_wait():
    bridge = SessionBridge()
    assert bridge.resolve_input(PlayerInput(message="hi")) is False


async def test_wait_for_input_returns_the_resolved_value():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)  # let wait_for_input reach its await point

    assert bridge.resolve_input(PlayerInput(message="hello")) is True
    assert await waiter == PlayerInput(message="hello")


async def test_resolve_input_is_a_noop_once_already_resolved():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    bridge.resolve_input(PlayerInput(message="first"))

    assert bridge.resolve_input(PlayerInput(message="second")) is False
    assert await waiter == PlayerInput(message="first")


async def test_wait_for_input_clears_pending_input_after_resolving():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    bridge.resolve_input(PlayerInput())
    await waiter

    assert bridge.pending_input is None


async def test_run_flow_pushes_flow_failed_onto_the_outbox_on_exception():
    from the_village.bridge import FlowFailed, run_flow

    bridge = SessionBridge()

    async def boom():
        raise ValueError("crew exploded")

    await run_flow(boom(), bridge)

    item = bridge.outbox.get_nowait()
    assert isinstance(item, FlowFailed)
    assert "crew exploded" in item.detail


async def test_run_flow_does_not_touch_the_outbox_on_success():
    from the_village.bridge import run_flow

    bridge = SessionBridge()

    async def fine():
        return None

    await run_flow(fine(), bridge)

    assert bridge.outbox.empty()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.bridge'`

- [ ] **Step 4: Implement `bridge.py`**

```python
# src/the_village/bridge.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from crewai import Agent


class FlowStatus(str, Enum):
    WAITING_FOR_TURN = "waiting_for_turn"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    DISCUSSION_COMPLETE = "discussion_complete"


@dataclass
class PlayerInput:
    message: str | None = None  # None means "passed"


@dataclass
class SessionBridge:
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | Death |
    FlowStatus); `pending_input` carries the one UI -> Flow value a paused
    Flow step is waiting on. Reused for every pause point across the whole
    session (the death-announcement gate, every discussion turn) rather than
    built fresh per pause, so ui.py has one bridge per session to hold in
    `gr.State`.
    """

    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    pending_input: asyncio.Future[PlayerInput] | None = None
    task: asyncio.Task | None = None
    agents: dict[str, Agent] = field(default_factory=dict)

    async def wait_for_input(self) -> PlayerInput:
        self.pending_input = asyncio.get_event_loop().create_future()
        try:
            return await self.pending_input
        finally:
            self.pending_input = None

    def resolve_input(self, player_input: PlayerInput) -> bool:
        """Unblock a paused `wait_for_input()`. False if nothing is pending
        (or it was already resolved) -- callers treat that as a no-op,
        the session-scoped replacement for Gradio's removed concurrency_id
        double-click guard."""
        if self.pending_input is None or self.pending_input.done():
            return False
        self.pending_input.set_result(player_input)
        return True


@dataclass
class FlowFailed:
    detail: str = "Something went wrong."


async def run_flow(coro, bridge: SessionBridge) -> None:
    """Runs a background Flow coroutine, turning an exception into a
    FlowFailed on the outbox instead of an unawaited-task traceback that
    never reaches the player. Wrap VillageFlow.kickoff_async(...) in this
    whenever it's launched as a background asyncio.Task."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: anything
        # from here must reach the player via the outbox, not vanish.
        await bridge.outbox.put(FlowFailed(detail=str(exc)))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/the_village/bridge.py tests/test_bridge.py
git commit -m "feat: add SessionBridge for async Flow<->Gradio hand-off"
```

---

## Task 2: Turn-`Crew` builders in `discussion.py`

**Files:**
- Modify: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: nothing new from Task 1 directly (this task's functions don't take a `SessionBridge`).
- Produces: `TurnOutput` (trimmed: `has_something_to_say: bool`, `message: str | None`), `AddressResolution` (unchanged), `DECLINED_TO_RESPOND` (unchanged constant), `_last_speaker_today(state: GameState) -> str | None`, `_resolve_target(candidate: str | None, state: GameState, exclude: str) -> str | None`, `_record_message(state: GameState, speaker: str, message: str, addressed_to: str | None) -> DiscussionMessage`, `async def _run_ai_turn(speaker: Agent, analyst: Agent, state: GameState, name: str, addressed_by: DiscussionMessage | None) -> DiscussionMessage | None`, `async def _run_player_turn(analyst: Agent, state: GameState, bridge: SessionBridge, name: str, addressed_by: DiscussionMessage | None) -> DiscussionMessage | None`.

This task adds the new turn-`Crew` machinery *alongside* the existing `DiscussionRunner`/`advance()` code (which Task 4 deletes) — `_last_speaker_today` and `_resolve_target` change signature (drop the `runner` param in favor of `state` directly), so this task also updates their existing call sites inside the old code so the file keeps working mid-refactor.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_discussion.py` (new imports at top; `MagicMock`/`AsyncMock` for `Crew.akickoff`):

```python
import asyncio
from unittest.mock import AsyncMock, patch

from the_village.discussion import (
    AddressResolution,
    DECLINED_TO_RESPOND,
    TurnOutput,
    _record_message,
    _resolve_target,
    _run_ai_turn,
    _run_player_turn,
)
from the_village.bridge import PlayerInput, SessionBridge


def make_discussion_state() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
    ]
    return GameState(player_name="Dana", day_number=1, villagers=villagers)


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def test_turn_output_has_no_addressed_to_field():
    assert "addressed_to" not in TurnOutput.model_fields


def test_record_message_appends_to_state_discussion_and_returns_it():
    state = make_discussion_state()
    msg = _record_message(state, "A", "hello", addressed_to="B")
    assert state.discussion == [msg]
    assert msg.speaker == "A"
    assert msg.message == "hello"
    assert msg.addressed_to == "B"
    assert msg.day_number == 1


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_discussion_state()
    assert _resolve_target(None, state, exclude="A") is None
    assert _resolve_target("A", state, exclude="A") is None
    assert _resolve_target("Ghost", state, exclude="A") is None
    assert _resolve_target("B", state, exclude="A") == "B"


async def test_run_ai_turn_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(TurnOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=object(), analyst=object(), state=state, name="A", addressed_by=None
        )
    assert message is None
    assert state.discussion == []


async def test_run_ai_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "B", "Where were you?", addressed_to="A")
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(TurnOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=object(), analyst=object(), state=state, name="A", addressed_by=asking
        )
    assert message.speaker == "A"
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_ai_turn_records_message_and_resolved_address():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                TurnOutput(has_something_to_say=True, message="I saw B leave."),
                AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=object(), analyst=object(), state=state, name="A", addressed_by=None
        )
    assert message.message == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.discussion == [message]


async def test_run_ai_turn_discards_addressed_to_from_the_analyst_on_decline():
    """Even if the analyst task somehow returns an address for a decline (it
    still runs -- see the spec's resolved decision to keep one uniform
    two-task Crew shape), a decline's recorded message must not carry it."""
    state = make_discussion_state()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                TurnOutput(has_something_to_say=False), AddressResolution(addressed_to="B")
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=object(), analyst=object(), state=state, name="A", addressed_by=None
        )
    assert message is None


async def test_run_player_turn_returns_none_on_scheduled_pass():
    state = make_discussion_state()
    bridge = SessionBridge()
    bridge.resolve_input(PlayerInput(message=None))
    message = await _run_player_turn(
        analyst=object(), state=state, bridge=bridge, name="Dana", addressed_by=None
    )
    assert message is None
    assert state.discussion == []


async def test_run_player_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "A", "Where were you?", addressed_to="Dana")
    bridge = SessionBridge()
    bridge.resolve_input(PlayerInput(message=None))
    message = await _run_player_turn(
        analyst=object(), state=state, bridge=bridge, name="Dana", addressed_by=asking
    )
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_player_turn_resolves_address_via_the_analyst():
    state = make_discussion_state()
    bridge = SessionBridge()
    bridge.resolve_input(PlayerInput(message="B, where were you?"))
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(AddressResolution(addressed_to="B"))),
    ):
        message = await _run_player_turn(
            analyst=object(), state=state, bridge=bridge, name="Dana", addressed_by=None
        )
    assert message.speaker == "Dana"
    assert message.message == "B, where were you?"
    assert message.addressed_to == "B"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v -k "record_message or resolve_target or run_ai_turn or run_player_turn or turn_output"`
Expected: FAIL — `_record_message`, `_run_ai_turn`, `_run_player_turn` don't exist yet; `TurnOutput` still has `addressed_to`.

- [ ] **Step 3: Implement the turn-Crew machinery**

In `src/the_village/discussion.py`:

1. Add `from crewai import Crew, Process, Task` to the imports (alongside the existing `from crewai import Agent`).
2. Add `from the_village.bridge import SessionBridge` to the imports.
3. Replace the `TurnOutput` class (originally at lines 193–225) with:

```python
class TurnOutput(BaseModel):
    has_something_to_say: bool = Field(
        description=(
            "Whether you have something to say right now. False means you'll "
            "sit this turn out."
        )
    )
    message: str | None = Field(
        default=None,
        description=(
            "What you say, if you have something to say. Keep it to one or "
            "two sentences -- brief, like real spoken dialogue."
        ),
    )
```

4. Replace `_last_speaker_today` (originally lines 145–149) with the `state`-only signature:

```python
def _last_speaker_today(state: GameState) -> str | None:
    today_messages = [m for m in state.discussion if m.day_number == state.day_number]
    return today_messages[-1].speaker if today_messages else None
```

5. Replace `_resolve_target` (originally lines 183–190) with the `state`-only signature:

```python
def _resolve_target(candidate: str | None, state: GameState, exclude: str) -> str | None:
    if not candidate or candidate == exclude:
        return None
    if candidate not in _living_participant_names(state):
        return None
    return candidate
```

6. Add the new prompt builders, `_record_message`, and the two turn-runners (near the bottom of the file, replacing where `_ask_agent`/`_infer_player_target`/`_generate_bonus_reply` used to be — those are deleted in Task 4, but leave them in place for now so the file still imports cleanly):

```python
def _record_message(
    state: GameState, speaker: str, message: str, addressed_to: str | None
) -> DiscussionMessage:
    msg = DiscussionMessage(
        day_number=state.day_number,
        speaker=speaker,
        message=message,
        addressed_to=addressed_to,
    )
    state.discussion.append(msg)
    return msg


def _build_speak_prompt(state: GameState, addressed_by: DiscussionMessage | None) -> str:
    living_names = _living_participant_names(state)
    parts = [
        "Known facts:",
        _format_deaths(state),
        "",
        f"Living villagers: {', '.join(living_names)}, including "
        f"{state.player_name} (the human player).",
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
            "It's your turn. Decide whether you have something to say -- a "
            "statement, question, or accusation. If you have nothing to add, "
            "say so."
        )
    return "\n".join(parts)


def _build_analyze_prompt() -> str:
    return (
        "Determine who, if anyone, the message you were just given as "
        "context is directed at. If the speaker had nothing to say, there "
        "is nothing to analyze -- leave addressed_to unset."
    )


def _build_player_analyze_prompt(state: GameState, message: str) -> str:
    candidates = [n for n in _living_participant_names(state) if n != state.player_name]
    return "\n".join(
        [
            "Known facts:",
            _format_deaths(state),
            "",
            f"Living villagers: {', '.join(candidates)}.",
            "",
            "Discussion so far:",
            _format_history(state),
            "",
            f'{state.player_name} just said: "{message}"',
            "Who, if anyone, is this message directed at? A name that's "
            "merely mentioned doesn't count -- only someone actually being "
            "spoken to.",
        ]
    )


async def _run_ai_turn(
    speaker: Agent,
    analyst: Agent,
    state: GameState,
    name: str,
    addressed_by: DiscussionMessage | None,
) -> DiscussionMessage | None:
    speak_task = Task(
        description=_build_speak_prompt(state, addressed_by),
        agent=speaker,
        expected_output="A TurnOutput saying whether you have something to say.",
        output_pydantic=TurnOutput,
    )
    analyze_task = Task(
        description=_build_analyze_prompt(),
        agent=analyst,
        expected_output="An AddressResolution naming who, if anyone, was addressed.",
        output_pydantic=AddressResolution,
        context=[speak_task],
    )
    crew = Crew(
        agents=[speaker, analyst], tasks=[speak_task, analyze_task], process=Process.sequential
    )
    result = await crew.akickoff()
    turn = result.tasks_output[0].pydantic or TurnOutput(has_something_to_say=False)

    if not turn.has_something_to_say or not turn.message:
        if addressed_by is None:
            return None
        return _record_message(state, name, DECLINED_TO_RESPOND, addressed_to=None)

    resolution = result.tasks_output[1].pydantic or AddressResolution(addressed_to=None)
    addressed_to = _resolve_target(resolution.addressed_to, state, exclude=name)
    return _record_message(state, name, turn.message, addressed_to)


async def _resolve_player_address(
    analyst: Agent, state: GameState, message: str, exclude: str
) -> str | None:
    task = Task(
        description=_build_player_analyze_prompt(state, message),
        agent=analyst,
        expected_output="An AddressResolution naming who, if anyone, was addressed.",
        output_pydantic=AddressResolution,
    )
    crew = Crew(agents=[analyst], tasks=[task])
    result = await crew.akickoff()
    resolution = result.tasks_output[0].pydantic or AddressResolution(addressed_to=None)
    return _resolve_target(resolution.addressed_to, state, exclude=exclude)


async def _run_player_turn(
    analyst: Agent,
    state: GameState,
    bridge: SessionBridge,
    name: str,
    addressed_by: DiscussionMessage | None,
) -> DiscussionMessage | None:
    player_input = await bridge.wait_for_input()
    if player_input.message is None:
        if addressed_by is None:
            return None
        return _record_message(state, name, DECLINED_TO_RESPOND, addressed_to=None)

    addressed_to = await _resolve_player_address(
        analyst, state, player_input.message, exclude=name
    )
    return _record_message(state, name, player_input.message, addressed_to)
```

7. Update the two remaining old call sites that used the previous `runner`-based signatures of `_last_speaker_today`/`_resolve_target` so the file still imports and the *old* tests (not yet deleted) keep passing: in `_build_round`, change `_last_speaker_today(runner)` to `_last_speaker_today(runner.state)`; in `_resolve_target` call sites inside `advance`/`_run_ai_turns`/`_generate_bonus_reply`, change `_resolve_target(candidate, runner, exclude=...)` to `_resolve_target(candidate, runner.state, exclude=...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS on all new tests; the pre-existing `DiscussionRunner`/`advance()` tests also still pass (their `_last_speaker_today`/`_resolve_target` call sites were updated, not their behavior).

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: add turn-Crew builders (speak + analyze tasks) to discussion.py"
```

---

## Task 3: `DiscussionFlow`

**Files:**
- Modify: `src/the_village/discussion.py`
- Test: `tests/test_discussion.py`

**Interfaces:**
- Consumes: `_run_ai_turn`, `_run_player_turn`, `_last_speaker_today`, `_avoid_immediate_repeat` (unchanged from today), `_living_participant_names` (unchanged), `_build_agent`, `_build_address_resolver` (unchanged), `SessionBridge`, `FlowStatus`, `PlayerInput` from `the_village.bridge`.
- Produces: `class DiscussionFlow(Flow)` with `def __init__(self, bridge: SessionBridge, rng: random.Random | None = None)` and `async def kickoff_async(self, inputs: dict | None = None) -> list[DiscussionMessage]` (inherited from `Flow`, returns the day's transcript). Constructed as `DiscussionFlow(bridge=bridge).kickoff_async(inputs=game_state.model_dump())`.

`DiscussionFlow` does **not** parameterize `Flow[GameState]` by object reference — CrewAI's `Flow` state is populated from `inputs` (a dict, reconstructing a model instance), not by handing it an existing object to mutate in place. `DiscussionFlow` gets its own `self.state: GameState`, built from `VillageFlow`'s state via `inputs=self.state.model_dump()`; `VillageFlow` copies the returned transcript back afterward (`self.state.discussion = transcript`) rather than relying on shared object identity. This sidesteps guessing at CrewAI's state-hydration internals in favor of the one pattern already proven working in this codebase (`VillageFlow.setup_game` populating `self.state` from `kickoff(inputs={"player_name": ...})`).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_discussion.py
from the_village.discussion import DiscussionFlow
from the_village.bridge import FlowStatus


def make_discussion_flow_state() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="werewolf", is_pack_leader=True),
        Villager(name="D", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=1, villagers=villagers)


def _decline_result():
    return _crew_result(TurnOutput(has_something_to_say=False), None)


async def test_discussion_flow_runs_two_rounds_where_everyone_gets_a_turn():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        flow = DiscussionFlow(bridge=bridge, rng=random.Random(1))
        transcript = await flow.kickoff_async(inputs=make_discussion_flow_state().model_dump())

    # Everyone declines every turn in this test, so no DiscussionMessages are
    # recorded -- what's under test is that the flow runs to completion
    # (doesn't hang) across two full rounds without error.
    assert transcript == []


async def test_discussion_flow_pushes_agents_onto_the_bridge():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        flow = DiscussionFlow(bridge=bridge, rng=random.Random(1))
        await flow.kickoff_async(inputs=make_discussion_flow_state().model_dump())

    assert set(bridge.agents.keys()) == {"A", "B", "C", "D"}


class NoShuffleRandom:
    """A `random.Random` stand-in whose `shuffle` is a no-op, so a round's
    order is exactly `_living_participant_names`' insertion order -- lets a
    test assert on *which* participant produces which scripted response
    without depending on a real shuffle's output for a given seed."""

    def shuffle(self, _seq):
        pass

    def randrange(self, start, _stop):
        return start


async def test_discussion_flow_resolves_a_bonus_reply_chain():
    bridge = SessionBridge()
    # make_discussion_flow_state()'s villagers list is [Dana, A, B, C, D];
    # with no shuffling, round order is exactly that -- Dana first (the
    # player, auto-passes below), then A, whose scripted response addresses
    # B; B's own scripted decline stops the chain there.
    state = make_discussion_flow_state()

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
        patch("the_village.discussion.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        flow = DiscussionFlow(bridge=bridge, rng=NoShuffleRandom())
        transcript = await flow.kickoff_async(inputs=state.model_dump())

    addressed_messages = [m for m in transcript if m.message == "B, where were you?"]
    assert len(addressed_messages) == 1
    assert addressed_messages[0].speaker == "A"
    assert addressed_messages[0].addressed_to == "B"
    decline_replies = [
        m for m in transcript if m.speaker == "B" and m.message == DECLINED_TO_RESPOND
    ]
    assert len(decline_replies) == 1


async def test_discussion_flow_pauses_for_player_and_resumes():
    bridge = SessionBridge()
    state = make_discussion_flow_state()

    async def scripted_akickoff(*_args, **_kwargs):
        return _decline_result()

    with patch("the_village.discussion.Crew.akickoff", new=scripted_akickoff):
        flow = DiscussionFlow(bridge=bridge, rng=random.Random(1))
        task = asyncio.create_task(flow.kickoff_async(inputs=state.model_dump()))

        # Drain the outbox until we see the player's turn status, then answer.
        seen_waiting_for_turn = False
        for _ in range(200):
            item = await bridge.outbox.get()
            if item == FlowStatus.WAITING_FOR_TURN:
                seen_waiting_for_turn = True
                bridge.resolve_input(PlayerInput(message=None))
                break
        assert seen_waiting_for_turn

        transcript = await task

    assert isinstance(transcript, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discussion.py -v -k discussion_flow`
Expected: FAIL — `DiscussionFlow` doesn't exist.

- [ ] **Step 3: Implement `DiscussionFlow`**

Add to `src/the_village/discussion.py` (imports: `from crewai.flow import Flow, start`; `import random`; `import asyncio` is already implied via `bridge.py`'s awaits, no direct `asyncio` import needed here):

```python
class DiscussionFlow(Flow[GameState]):
    def __init__(self, bridge: SessionBridge, rng: random.Random | None = None):
        super().__init__()
        self.bridge = bridge
        self.rng = rng or random.Random()
        self._speaker_agents: dict[str, Agent] = {}
        self._analyst: Agent = _build_address_resolver()

    @start()
    async def run_rounds(self) -> list[DiscussionMessage]:
        living_ai = [
            v
            for v in self.state.villagers
            if v.is_alive and v.player_type in ("villager", "werewolf")
        ]
        self._speaker_agents = {
            v.name: _build_agent(v, self.state.villagers) for v in living_ai
        }
        self.bridge.agents = self._speaker_agents

        for _ in range(2):
            await self._run_round()

        return self.state.discussion

    async def _run_round(self) -> None:
        order = _living_participant_names(self.state)
        self.rng.shuffle(order)
        while order:
            if _avoid_immediate_repeat(order, _last_speaker_today(self.state), self.rng):
                # The only entry left in this round would repeat the last
                # speaker and no one else is available to swap in -- skip
                # them for this round rather than force a repeat; round two
                # covers everyone again regardless.
                order.pop(0)
                continue
            name = order.pop(0)
            message = await self._run_turn(name, addressed_by=None)
            if message is not None:
                await self.bridge.outbox.put(message)
                await self._resolve_address_chain(message)

    async def _run_turn(
        self, name: str, addressed_by: DiscussionMessage | None
    ) -> DiscussionMessage | None:
        if name == self.state.player_name:
            status = FlowStatus.WAITING_FOR_ANSWER if addressed_by else FlowStatus.WAITING_FOR_TURN
            await self.bridge.outbox.put(status)
            return await _run_player_turn(
                self._analyst, self.state, self.bridge, name, addressed_by
            )
        return await _run_ai_turn(
            self._speaker_agents[name], self._analyst, self.state, name, addressed_by
        )

    async def _resolve_address_chain(
        self, message: DiscussionMessage, chain: frozenset[str] = frozenset()
    ) -> None:
        if message.addressed_to is None:
            return
        target = message.addressed_to
        reply = await self._run_turn(target, addressed_by=message)
        if reply is None:
            return
        await self.bridge.outbox.put(reply)
        chain = chain | {message.speaker, target}
        if reply.addressed_to is not None and reply.addressed_to not in chain:
            await self._resolve_address_chain(reply, chain)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS on all tests (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "feat: add DiscussionFlow (two-round loop + bonus-reply chain)"
```

---

## Task 4: Delete the old orchestration and finish `test_discussion.py`

**Files:**
- Modify: `src/the_village/discussion.py`
- Modify: `tests/test_discussion.py`

**Interfaces:**
- Consumes: everything from Tasks 2–3 stays; nothing new produced.

- [ ] **Step 1: Delete dead code from `discussion.py`**

Remove (all now unused — `DiscussionFlow` replaced their role):
- `INITIAL_BUDGET` constant
- `DiscussionRunner` dataclass
- `start_discussion`
- `_active_participants`
- `_build_round`
- `_ask_agent`
- `_build_prompt` (superseded by `_build_speak_prompt`)
- `_infer_player_target` (superseded by `_resolve_player_address`)
- `DECLINED_TO_RESPOND`'s old producer `_generate_bonus_reply`
- `_run_bonus_reply`
- `AdvanceStatus` (superseded by `FlowStatus` in `bridge.py`)
- `advance`
- `_run_ai_turns`

Keep: `AddressResolution`, `_build_address_resolver`, `_build_agent`, `_weekday`, `_format_deaths`, `_format_history`, `_living_participant_names`, `_avoid_immediate_repeat`, `_last_speaker_today`, `_resolve_target`, `DECLINED_TO_RESPOND`, `TurnOutput`, `_record_message`, all the `_build_*_prompt` functions, `_run_ai_turn`, `_resolve_player_address`, `_run_player_turn`, `DiscussionFlow`.

- [ ] **Step 2: Delete the now-obsolete tests from `tests/test_discussion.py`**

Remove every test that exercises the deleted API: all `test_start_discussion_*`, `test_active_participants_*`, `test_build_round_*`, `test_infer_player_target_*`, `test_generate_bonus_reply_*`, `test_build_prompt_*`, and every `test_advance_*` test. Remove the now-unused `ScriptedAgent`/`StaticResolver`/`BoomResolver` helper classes and `make_runner` if nothing else references them (check first — `make_state`/`make_runner` may still be used by a kept test; `make_discussion_state`/`make_discussion_flow_state` from Tasks 2–3 are the replacements going forward).

- [ ] **Step 3: Run the full test suite to confirm nothing references deleted names**

Run: `uv run pytest tests/test_discussion.py -v`
Expected: PASS, only the tests added in Tasks 2–3 remain (plus the small formatting-helper tests: `test_format_deaths_*`, `test_format_history_*`, `test_weekday`-adjacent tests if present — keep those, they test unchanged helpers).

- [ ] **Step 4: Commit**

```bash
git add src/the_village/discussion.py tests/test_discussion.py
git commit -m "refactor: remove budget-based DiscussionRunner/advance() orchestration"
```

---

## Task 5: `VillageFlow` becomes the async orchestrator

**Files:**
- Modify: `src/the_village/main.py`
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: `DiscussionFlow`, `_record_message`-produced transcript shape (`list[DiscussionMessage]`) from Task 3; `SessionBridge`, `FlowStatus`, `PlayerInput` from `bridge.py`.
- Produces: `class VillageFlow(Flow[GameState])` with `def __init__(self, bridge: SessionBridge)`; `async def kickoff()` (module-level CLI entry point, replaces the old sync one); `def plot()` (unchanged).

- [ ] **Step 1: Write the failing test**

Replace `tests/test_flow.py` entirely:

```python
# tests/test_flow.py
import asyncio

from the_village.bridge import PlayerInput, SessionBridge
from the_village.main import VillageFlow


async def test_village_flow_produces_valid_night_one_result_and_pauses_for_discussion():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": "Dana"}))

    # Drain until the death-announcement pause, then unblock it. We don't
    # drive a full discussion round here (that's DiscussionFlow's own test
    # suite in test_discussion.py) -- just confirm VillageFlow reaches and
    # respects the gate, then cancel rather than run a real discussion.
    from the_village.state import Death

    saw_death = False
    for _ in range(50):
        item = await bridge.outbox.get()
        if isinstance(item, Death):
            saw_death = True
            bridge.resolve_input(PlayerInput())
            break
    assert saw_death

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state = flow.state
    assert len(state.villagers) == 7
    assert len(state.deaths) == 1

    death = state.deaths[0]
    assert death.day_number == 2

    killed = next(v for v in state.villagers if v.name == death.name)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.villagers if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flow.py -v`
Expected: FAIL — `VillageFlow()` doesn't accept a `bridge` kwarg yet.

- [ ] **Step 3: Rewrite `main.py`**

```python
#!/usr/bin/env python
import asyncio

from crewai.flow import Flow, listen, start

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion import DiscussionFlow
from the_village.night import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState


class VillageFlow(Flow[GameState]):
    def __init__(self, bridge: SessionBridge):
        super().__init__()
        self.bridge = bridge

    @start()
    async def setup_game(self):
        roster_state = build_initial_roster(self.state.player_name)
        self.state.day_number = roster_state.day_number
        self.state.villagers = roster_state.villagers

    @listen(setup_game)
    async def run_night_one(self):
        resolve_night_one(self.state)

    @listen(run_night_one)
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.deaths[-1])
        await self.bridge.wait_for_input()

    @listen(announce_death)
    async def run_discussion(self):
        transcript = await DiscussionFlow(bridge=self.bridge).kickoff_async(
            inputs=self.state.model_dump()
        )
        self.state.discussion = transcript
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)


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
            bridge.resolve_input(PlayerInput(message=None))


async def _kickoff_async():
    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(village_flow.kickoff_async(inputs={"player_name": "TestPlayer"}))
    consumer_task = asyncio.create_task(_auto_play_consumer(bridge))
    await flow_task
    consumer_task.cancel()
    print(village_flow.state.model_dump_json(indent=2))


def kickoff():
    asyncio.run(_kickoff_async())


def plot():
    village_flow = VillageFlow(bridge=SessionBridge())
    village_flow.plot()


if __name__ == "__main__":
    kickoff()
```

Note on `announce_death`: it pushes the `Death` object (from `state.py`, already a model — no new wrapper type needed) then calls `bridge.wait_for_input()` directly rather than manually managing `bridge.pending_input` — `wait_for_input()` already does that (see Task 1), and the death gate doesn't need the returned `PlayerInput`'s content, just the unblock signal.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/main.py tests/test_flow.py
git commit -m "feat: make VillageFlow the async session orchestrator through discussion"
```

---

## Task 6: `ui.py` rewiring

**Files:**
- Modify: `src/the_village/ui.py`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `SessionBridge`, `PlayerInput`, `FlowStatus`, `FlowFailed`, `run_flow` from `bridge.py`; `VillageFlow` from `main.py`; `cast_votes` from `voting.py` (unchanged signature, now called with `bridge.agents` instead of `runner.agents`).

This task's diff is concentrated in four functions: `start_game`, `begin_discussion` → replaced by a `begin_discussion`-shaped resume, `send_discussion_turn`, `pass_discussion_turn`, plus the `Blocks` wiring at the bottom of `build_app()`. Formatting helpers (`format_event_log`, `format_discussion_transcript`, panel formatters, CSS/JS builders) are unchanged — leave them exactly as-is.

- [ ] **Step 1: Write the failing tests**

Replace the `start_game`/`begin_discussion`/`send_discussion_turn`/`pass_discussion_turn`-related tests in `tests/test_ui.py` (search for `test_start_game_*`; the discussion-turn-driving tests further down the file, if any beyond what was shown, follow the same pattern) with:

```python
# tests/test_ui.py additions
import asyncio

from the_village.bridge import FlowFailed, FlowStatus, PlayerInput, SessionBridge
from the_village.state import GameState
from the_village.ui import begin_discussion, pass_discussion_turn, send_discussion_turn, start_game


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
        update async for update in begin_discussion(bridge, GameState(day_number=1))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v -k "start_game or begin_discussion or send_discussion_turn or pass_discussion_turn"`
Expected: FAIL — current `start_game`/`begin_discussion`/etc. are sync generators with the old `DiscussionRunner`-based signature.

- [ ] **Step 3: Rewrite the driving functions in `ui.py`**

Replace `begin_discussion`, `send_discussion_turn`, `pass_discussion_turn` (originally lines 406–467), `start_game` (originally lines 548–570), and `_drive_discussion` (originally lines 353–403) with:

```python
async def _stream_bridge(bridge: SessionBridge, state: GameState):
    """Drains bridge.outbox, yielding a UI-update tuple per item, until a
    status tells the caller to stop and hand control back to the player."""
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, DiscussionMessage):
                transcript = format_discussion_transcript(state)
                yield (
                    bridge,
                    transcript,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
                time.sleep(SPEAKER_THINKING_DELAY_SECONDS)
            elif item == FlowStatus.WAITING_FOR_TURN or item == FlowStatus.WAITING_FOR_ANSWER:
                yield (
                    bridge,
                    gr.update(),
                    gr.update(value=""),
                    gr.update(visible=True),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
                return
            elif item == FlowStatus.DISCUSSION_COMPLETE:
                yield (
                    bridge,
                    gr.update(),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(
                        visible=True, value="The Moderator has ended the discussion."
                    ),
                    gr.update(visible=True),
                    gr.update(),
                )
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


async def begin_discussion(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput()):
        return  # already resolved (e.g. double-click) -- no-op
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(value=f"### {weekday}'s Discussion", visible=True),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def send_discussion_turn(bridge: SessionBridge, state: GameState, message: str):
    if not message.strip():
        yield (gr.skip(),) * 7
        return
    if not bridge.resolve_input(PlayerInput(message=message.strip())):
        return
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def pass_discussion_turn(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput(message=None)):
        return
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    bridge.task = asyncio.create_task(
        run_flow(
            village_flow.kickoff_async(inputs={"player_name": player_name.strip()}), bridge
        )
    )

    item = await bridge.outbox.get()
    if isinstance(item, FlowFailed):
        raise gr.Error("Something went wrong, please try again.")

    state = village_flow.state
    yield (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
        format_alive_panel(state),
        format_lynched_panel(state),
        state,
        bridge,
    )
```

Add `import asyncio` at the top of `ui.py`, and `from the_village.bridge import FlowFailed, FlowStatus, PlayerInput, SessionBridge, run_flow` alongside the existing imports; drop the now-unused `from the_village.discussion import AdvanceStatus, DiscussionRunner, advance, start_discussion` import entirely (nothing from `discussion.py` is referenced directly in `ui.py`) — keep `from the_village.state import DiscussionMessage` (already imported) and `from the_village.main import VillageFlow` (already imported).

- [ ] **Step 4: Update `build_app()`'s wiring**

In `build_app()` (originally lines 573–730):

1. Add a new `gr.State()` for the bridge: `session_bridge = gr.State()`, replacing `discussion_runner_state = gr.State()`.
2. `start_game`'s `outputs=` list gains `session_bridge` as an 8th output (it now returns `bridge` too).
3. Every `inputs=[...]` list that referenced `discussion_runner_state` now references `session_bridge`, and every handler that took `runner` as its first arg now takes `bridge` (and, per the new signatures above, also `game_state` where the function needs `state` for formatting — `begin_discussion`, `send_discussion_turn`, and `pass_discussion_turn` all take `(bridge, state, ...)`, so their `inputs=` lists become `[session_bridge, game_state]` or `[session_bridge, game_state, discussion_textbox]`).
4. Remove `concurrency_limit=1, concurrency_id="discussion_turn"` from all four `.click()`/`.submit()` calls that had it (`begin_discussion_button.click`, `send_button.click`, `discussion_textbox.submit`, `pass_button.click`) — per the Global Constraints, the `resolve_input`/no-pending-future no-op is the replacement guard, scoped correctly per-session.
5. Remove `concurrency_limit=1, concurrency_id="vote_cast"` from the two vote-casting `.click()` calls for the same reason — but voting doesn't yet have its own bridge-based guard (it's untouched per the spec), so leave a short comment noting this is an accepted, unguarded double-click risk carried forward unchanged, not something this task fixes (voting is explicitly out of scope).

   Actually — re-read the Global Constraint: it says drop concurrency_id "on the discussion/night event listeners" specifically, not voting's. Voting's `concurrency_id="vote_cast"` stays exactly as it is today; only the four discussion-turn listeners lose theirs. Do not touch the vote-casting `.click()` calls at all.
6. `begin_discussion_button.click(fn=begin_discussion, inputs=[session_bridge, game_state], outputs=discussion_outputs)` — note `discussion_outputs`'s first slot is now `session_bridge` (was `discussion_runner_state`).
7. Voting handlers (`cast_player_vote`, `cast_player_abstain` in `voting.py`-adjacent code) currently take `discussion_runner_state` to reach `runner.agents` — change their `inputs=` to pass `session_bridge` instead, and inside `cast_player_vote`, change `cast_votes(state, runner.agents, player_vote=target)` to `cast_votes(state, bridge.agents, player_vote=target)` (the parameter name in the function signature changes from `runner: DiscussionRunner` to `bridge: SessionBridge`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS on every test from Step 1, plus the pre-existing formatting-helper tests (`test_format_event_log_*`, `test_format_deaths_panel_*`, `test_format_alive_panel_*`, `test_format_discussion_transcript_*`), which are untouched by this task.

- [ ] **Step 6: Manual sanity check that the app still boots**

Run: `uv run app` (or `uv run python -m the_village.ui`), confirm the Gradio server starts without import errors. Ctrl-C to stop — full interactive verification happens in Task 8.

- [ ] **Step 7: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: rewire ui.py onto SessionBridge, drop global concurrency_id"
```

---

## Task 7: Concurrency smoke test

**Files:**
- Test: `tests/test_concurrency.py`

**Interfaces:**
- Consumes: `VillageFlow`, `SessionBridge`, `PlayerInput`, `FlowStatus`, `_auto_play_consumer`-equivalent draining loop (write a local one in the test rather than importing `main.py`'s CLI-only helper, to keep this test independent of CLI wiring).

- [ ] **Step 1: Write the test**

```python
# tests/test_concurrency.py
import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion import AddressResolution, TurnOutput
from the_village.main import VillageFlow


def _decline_result():
    return SimpleNamespace(
        tasks_output=[
            SimpleNamespace(pydantic=TurnOutput(has_something_to_say=False)),
            SimpleNamespace(pydantic=AddressResolution(addressed_to=None)),
        ]
    )


async def _run_one_session(player_name: str) -> str:
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(flow.kickoff_async(inputs={"player_name": player_name}))

    while True:
        item = await bridge.outbox.get()
        if item == FlowStatus.DISCUSSION_COMPLETE:
            break
        bridge.resolve_input(PlayerInput(message=None))

    await flow_task
    return flow.state.player_name


async def test_two_village_flows_complete_independently_when_run_concurrently():
    with patch(
        "the_village.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())
    ):
        results = await asyncio.gather(
            _run_one_session("Alice"),
            _run_one_session("Bob"),
        )

    assert set(results) == {"Alice", "Bob"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_concurrency.py -v`
Expected: FAIL initially if there's any shared-state leakage from earlier tasks (there shouldn't be, given `GameState`/`SessionBridge` are constructed fresh per session) — if it fails, the failure itself is diagnostic of a real cross-session bug introduced by an earlier task, not a placeholder to fill in.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/test_concurrency.py -v`
Expected: PASS — this test needs no new production code if Tasks 1–6 were implemented correctly; it's a regression guard for the concurrency properties the spec called out, not a new feature.

- [ ] **Step 4: Commit**

```bash
git add tests/test_concurrency.py
git commit -m "test: verify two VillageFlow sessions run concurrently without interleaving"
```

---

## Task 8: Full suite + manual browser verification

**Files:** none (verification-only task)

- [ ] **Step 1: Run the full automated test suite**

Run: `uv run pytest -v`
Expected: PASS across all files, including `test_voting.py`, `test_roster.py`, `test_night.py`, `test_state.py` (untouched by this plan — confirms nothing here broke them).

- [ ] **Step 2: Manual browser pass**

Run: `uv run app`. In the browser:
1. Start a game, confirm the death announcement shows and "Begin Discussion" appears.
2. Click "Begin Discussion" — confirm AI turns stream in with the "typing" pacing, across two full rounds.
3. Type a message that addresses a named living villager by name — confirm you get a bonus reply, and possibly a further chained reply, without the discussion silently skipping your turn.
4. Pass on at least one of your own turns — confirm the discussion continues without you.
5. Confirm the discussion actually stops after two rounds (not run indefinitely) and "Begin Voting" appears.
6. Open a second browser tab/window, start a second game concurrently while the first is mid-discussion — confirm neither session's transcript blocks or bleeds into the other's (this is the manual counterpart to Task 7's automated check, now exercised end-to-end through real Gradio event dispatch).
7. Proceed through to voting in one of the sessions — confirm `cast_votes` still works (villager AI votes get cast, tally displays, a lynch outcome or tie is announced) — this confirms the `bridge.agents` hand-off from Task 6 Step 4.7 is wired correctly.

- [ ] **Step 3: Report results**

If every check in Step 2 passes, the redesign is complete and matches the spec. If anything fails, note exactly which step and what was observed — do not mark this task done until the manual pass is clean, since this is the only place UI-level regressions (unreachable via unit tests, e.g. Gradio wiring mistakes in Task 6 Step 4) would surface.
