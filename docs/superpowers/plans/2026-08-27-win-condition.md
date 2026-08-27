# Win Condition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect a winner (villagers or werewolves) after every kill and every lynch, end `VillageFlow` naturally when one is found, and show the result — including who the werewolves were — in a new panel in the Gradio UI, with a "Play Again" button that starts an entirely new game.

**Architecture:** A `GameState.determine_winner()` method encodes the win rule (no living werewolves → villagers; living werewolves ≥ living non-werewolves → werewolves). Two symmetric `VillageFlow` router steps — `check_winner_after_kill` and `check_winner_after_lynching` — call it right after each of the two places a death happens, routing either back into the normal day/night cycle or into a terminal `finish_game` step that puts a `GameOverResult` on the bridge. `ui.py` adds a `GameOverResult` branch to every generator that can observe the end of the game, revealing a new results panel alongside the existing history log.

**Tech Stack:** Python, crewAI `Flow`, Pydantic, Gradio, pytest (`pytest-asyncio`, asyncio mode already configured for this repo's async `def test_*` functions).

**Spec:** `docs/superpowers/specs/2026-08-27-win-condition-design.md`

## Global Constraints

- Never use YAML for CrewAI config in this repo — jsonc/code only (per `AGENTS.md`). Not touched by this plan, but any new config must honor it.
- Werewolves can only die by lynching (`WereWolfPack._eligible_targets` excludes werewolves) — `determine_winner()` relies on this to treat "no living werewolves" and "every werewolf lynched" as equivalent.
- A game-ending lynch must not call `GameState.advance_day()` — no dangling extra `Day` once the game is over.
- The results panel sits **alongside** `history_log`, not replacing it (confirmed during design review).
- "Play Again" must construct an entirely new `VillageFlow` (confirmed during design review) — reuse `start_game`, don't special-case restart logic.

---

## Task 1: Win-condition logic on `GameState`

**Files:**
- Modify: `src/the_village/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `Winner = Literal["villagers", "werewolves"]`; `GameState.winner: Winner | None` field (defaults `None`); `GameState.living_werewolves_count() -> int`; `GameState.living_non_werewolves_count() -> int`; `GameState.determine_winner() -> Winner | None`; `GameState.werewolf_names() -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state.py`:

```python
def test_game_state_winner_defaults_to_none():
    state = GameState()
    assert state.winner is None


def test_living_werewolves_count_excludes_the_dead():
    players = [
        Player(name="A", player_type="werewolf"),
        Player(name="B", player_type="werewolf", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.living_werewolves_count() == 1


def test_living_non_werewolves_count_includes_the_user():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.living_non_werewolves_count() == 2


def test_determine_winner_is_none_when_werewolves_are_outnumbered():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.determine_winner() is None


def test_determine_winner_is_werewolves_at_parity():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.determine_winner() == "werewolves"


def test_determine_winner_is_werewolves_when_a_mislynch_tips_the_balance():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),  # mislynched
        Player(name="B", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.determine_winner() == "werewolves"


def test_determine_winner_is_villagers_once_every_werewolf_is_dead():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="werewolf", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.determine_winner() == "villagers"


def test_determine_winner_is_villagers_with_no_werewolves_in_the_roster():
    state = GameState(players=[Player(name="Dana", player_type="user")])
    assert state.determine_winner() == "villagers"


def test_werewolf_names_lists_all_werewolves_dead_or_alive():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="werewolf", is_alive=False),
        Player(name="B", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.werewolf_names() == ["A", "B"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py -k "winner or living_werewolves or living_non_werewolves or werewolf_names" -v`
Expected: FAIL with `AttributeError` (no `winner` field / no such methods) or a pydantic validation error.

- [ ] **Step 3: Implement**

In `src/the_village/state.py`, add `Winner` next to `PlayerType` (after line 9):

```python
PlayerType = Literal["user", "villager", "werewolf"]

Winner = Literal["villagers", "werewolves"]
```

Add the `winner` field to `GameState` (in the class body, alongside `days`):

```python
class GameState(BaseModel):
    user_player_name: str = ""
    players: list[Player] = []
    days: list[Day] = Field(default_factory=lambda: [Day(day_number=1)])
    winner: Winner | None = None
```

Add the four new methods to `GameState`, right after `names_of_other_living_players` (currently ending at line 93):

```python
    def living_werewolves_count(self) -> int:
        return sum(1 for player in self.players if player.is_werewolf and player.is_alive)

    def living_non_werewolves_count(self) -> int:
        return sum(
            1 for player in self.players if player.is_not_werewolf and player.is_alive
        )

    def determine_winner(self) -> Winner | None:
        if self.living_werewolves_count() == 0:
            return "villagers"
        if self.living_werewolves_count() >= self.living_non_werewolves_count():
            return "werewolves"
        return None

    def werewolf_names(self) -> list[str]:
        return [player.name for player in self.players if player.is_werewolf]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/state.py tests/test_state.py
git commit -m "feat: add win-condition detection to GameState"
```

---

## Task 2: `GameOverResult` bridge message

**Files:**
- Modify: `src/the_village/bridge.py`
- Test: `tests/test_bridge.py`

**Interfaces:**
- Consumes: `Winner` from `the_village.state` (Task 1).
- Produces: `GameOverResult` dataclass with fields `winner: Winner` and `werewolf_names: list[str]`.

- [ ] **Step 1: Write the failing test**

Add to the top imports of `tests/test_bridge.py`:

```python
from the_village.bridge import FlowStatus, GameOverResult, PlayerInput, SessionBridge
```

Append:

```python
def test_game_over_result_carries_winner_and_werewolf_names():
    result = GameOverResult(winner="villagers", werewolf_names=["A", "B"])
    assert result.winner == "villagers"
    assert result.werewolf_names == ["A", "B"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bridge.py::test_game_over_result_carries_winner_and_werewolf_names -v`
Expected: FAIL with `ImportError: cannot import name 'GameOverResult'`

- [ ] **Step 3: Implement**

In `src/the_village/bridge.py`, add the import and the new dataclass. Change the top import block:

```python
from crewai import Agent

from the_village.state import Winner
```

Add the dataclass right after `PlayerInput` (before `SessionBridge`):

```python
@dataclass
class GameOverResult:
    winner: Winner
    werewolf_names: list[str]
```

Update `SessionBridge`'s docstring line describing the outbox's carried types — change:

```python
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | VoteOutcome |
    str | FlowStatus -- the death announcement is a bare str, the victim's
    name; VoteOutcome is the vote reveal, put on once everyone has voted);
```

to:

```python
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | VoteOutcome |
    GameOverResult | str | FlowStatus -- the death announcement is a bare
    str, the victim's name; VoteOutcome is the vote reveal, put on once
    everyone has voted; GameOverResult is the last item ever put on a
    given session's outbox, once a winner is decided);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/bridge.py tests/test_bridge.py
git commit -m "feat: add GameOverResult bridge message"
```

---

## Task 3: `VillageFlow` terminal routing

**Files:**
- Modify: `src/the_village/village_flow.py`
- Modify: `tests/test_flow.py`
- Modify: `tests/test_concurrency.py` (comment only)

**Interfaces:**
- Consumes: `GameState.determine_winner()`, `GameState.werewolf_names()` (Task 1); `GameOverResult` (Task 2).
- Produces: `VillageFlow` now completes naturally (no infinite loop) once a winner is decided; the bridge's outbox ends with a `GameOverResult`.

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `tests/test_flow.py`:

```python
from the_village.bridge import FlowStatus, GameOverResult, PlayerInput, SessionBridge
from the_village.state import Player
from the_village.village_flow import VillageFlow
from the_village.voting import VoteOutcome
```

(`Player` is new; the rest already exist — just add the `Player` import line alongside the existing ones.)

Append two new tests:

```python
async def test_village_flow_ends_the_game_when_a_night_kill_reaches_werewolf_parity():
    # 3 living non-werewolves (Dana, A, D) vs 2 werewolves -- one villager
    # dying tonight brings it to 2 vs 2, ending the game right after the
    # death is announced and "Begin" is clicked, before discussion starts.
    custom_roster = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="D", player_type="villager"),
        Player(name="B", player_type="werewolf", is_pack_leader=True),
        Player(name="C", player_type="werewolf"),
    ]
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)

    with patch(
        "the_village.village_flow.build_initial_roster", return_value=custom_roster
    ):
        task = asyncio.create_task(
            flow.kickoff_async(inputs={"user_player_name": "Dana"})
        )

        await bridge.outbox.get()  # the night's death announcement
        bridge.resolve_input(PlayerInput())  # -> Begin

        result = await bridge.outbox.get()

        await task  # the flow reaches its natural end -- no cancellation needed

    assert isinstance(result, GameOverResult)
    assert result.winner == "werewolves"
    assert set(result.werewolf_names) == {"B", "C"}
    assert flow.state.winner == "werewolves"


async def test_village_flow_ends_the_game_when_a_lynch_eliminates_the_last_werewolf():
    # 3 living non-werewolves (Dana, A, D) vs 1 werewolf (W) -- night one's
    # kill removes one villager (2 vs 1, game continues); the human then
    # votes to lynch W, leaving zero living werewolves.
    custom_roster = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="D", player_type="villager"),
        Player(name="W", player_type="werewolf", is_pack_leader=True),
    ]
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)

    with patch(
        "the_village.village_flow.build_initial_roster", return_value=custom_roster
    ), patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff):
        task = asyncio.create_task(
            flow.kickoff_async(inputs={"user_player_name": "Dana"})
        )

        await bridge.outbox.get()  # night one's death announcement
        bridge.resolve_input(PlayerInput())  # -> begin discussion

        result = None
        while result is None:
            item = await bridge.outbox.get()
            if item == FlowStatus.DISCUSSION_COMPLETE:
                bridge.resolve_input(PlayerInput())  # -> begin voting
            elif item == FlowStatus.WAITING_FOR_VOTE:
                bridge.resolve_input(PlayerInput(text="W"))  # vote to lynch the werewolf
            elif isinstance(item, GameOverResult):
                result = item
            elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER):
                bridge.resolve_input(PlayerInput(text=None))

        await task

    assert result.winner == "villagers"
    assert result.werewolf_names == ["W"]
    assert flow.state.winner == "villagers"
    assert flow.state.day_number == 1  # no dangling extra Day once the game ended
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_flow.py -k "ends_the_game" -v`
Expected: FAIL — either a timeout/hang (current flow loops forever with no `game_over` routing) or an import error for `GameOverResult`/`Player`.

- [ ] **Step 3: Implement**

In `src/the_village/village_flow.py`, update the import line:

```python
from the_village.bridge import FlowStatus, GameOverResult, PlayerInput, SessionBridge
```

Replace the whole flow-step section (from `@router(setup_game)` through `run_next_night`) with:

```python
    @router(setup_game)
    async def run_night_one(self):
        kill_first_victim(self.state)
        # Emitting "night_fell" (rather than listening on this method's own
        # name) lets announce_death listen on a single signal shared with
        # run_next_night's router below.
        return "night_fell"

    @listen("night_fell")
    async def announce_death(self):
        await self.bridge.outbox.put(self.state.current_day.player_found_dead)
        await self.bridge.wait_for_input()

    @router(announce_death)
    async def check_winner_after_kill(self):
        self.state.winner = self.state.determine_winner()
        return "game_over" if self.state.winner else "discussion"

    @listen("discussion")
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
        logger.debug(
            "VillageFlow.run_voting: flow=%s bridge=%s entering",
            id(self),
            id(self.bridge),
        )
        outcome = await Voting(
            state=self.state,
            bridge=self.bridge,
            player_agents=self._player_agents,
        ).run()
        logger.debug(
            "VillageFlow.run_voting: flow=%s bridge=%s voting runner returned, outcome=%s",
            id(self),
            id(self.bridge),
            outcome,
        )
        await self.bridge.outbox.put(outcome)
        await self.bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

    @router(run_voting)
    async def check_winner_after_lynching(self):
        self.state.winner = self.state.determine_winner()
        if self.state.winner:
            return "game_over"
        self.state.advance_day()
        return "continue_night"

    @router("continue_night")
    async def run_next_night(self):
        await WereWolfPack(self.state, self._player_agents).kill_next_victim()
        return "night_fell"

    @listen("game_over")
    async def finish_game(self):
        await self.bridge.outbox.put(
            GameOverResult(
                winner=self.state.winner,
                werewolf_names=self.state.werewolf_names(),
            )
        )
```

Now simplify the CLI smoke-test scaffolding at the bottom of the file, since the flow completes naturally once a winner is decided. Replace this whole block:

```python
# VillageFlow's day/night cycle has no win condition yet and loops forever,
# so the CLI smoke test below has to stop itself after a few days rather
# than waiting for the flow to finish on its own.
_AUTO_PLAY_MAX_DAYS = 3


async def _auto_play_consumer(bridge: SessionBridge, max_days: int) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines (and the
    player auto-abstains from voting) so the flow reaches completion as a
    smoke test rather than hanging forever. Returns once `max_days` votes
    have completed; the caller cancels the (otherwise endless) flow task.
    """
    days_voted = 0
    while True:
        item = await bridge.outbox.get()
        print(item)
        if item == FlowStatus.VOTING_COMPLETE:
            days_voted += 1
            if days_voted >= max_days:
                return
            continue
        if item in (
            FlowStatus.WAITING_FOR_TURN,
            FlowStatus.WAITING_FOR_ANSWER,
            FlowStatus.WAITING_FOR_VOTE,
            FlowStatus.DISCUSSION_COMPLETE,
        ) or not isinstance(item, FlowStatus):
            bridge.resolve_input(PlayerInput(text=None))


async def _kickoff_async():
    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(
        village_flow.kickoff_async(inputs={"user_player_name": "TestPlayer"})
    )
    consumer_task = asyncio.create_task(
        _auto_play_consumer(bridge, _AUTO_PLAY_MAX_DAYS)
    )
    await consumer_task
    flow_task.cancel()
    try:
        await flow_task
    except asyncio.CancelledError:
        pass
    print(village_flow.state.model_dump_json(indent=2))
```

with:

```python
async def _auto_play_consumer(bridge: SessionBridge) -> None:
    """Drains the outbox and auto-passes every pause point, unattended.

    Used by the CLI kickoff() (crewai run/test), which has no live Gradio
    session to answer pauses -- every AI/player turn auto-declines (and the
    player auto-abstains from voting) so the flow reaches its natural
    GameOverResult ending as a smoke test.
    """
    while True:
        item = await bridge.outbox.get()
        print(item)
        if isinstance(item, GameOverResult):
            return
        if item in (
            FlowStatus.WAITING_FOR_TURN,
            FlowStatus.WAITING_FOR_ANSWER,
            FlowStatus.WAITING_FOR_VOTE,
            FlowStatus.DISCUSSION_COMPLETE,
        ) or not isinstance(item, FlowStatus):
            bridge.resolve_input(PlayerInput(text=None))


async def _kickoff_async():
    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    flow_task = asyncio.create_task(
        village_flow.kickoff_async(inputs={"user_player_name": "TestPlayer"})
    )
    await _auto_play_consumer(bridge)
    await flow_task
    print(village_flow.state.model_dump_json(indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_flow.py -v`
Expected: PASS (all tests, old and new). This includes the four pre-existing tests, which still cancel their flow task early (the default 7-player roster needs 3 kills to reach werewolf parity, so it genuinely hasn't ended within 2 days) — only their comments describe stale reasoning next.

- [ ] **Step 5: Update stale comments describing the old "loops forever" behavior**

In `tests/test_flow.py`, both `test_village_flow_reaches_voting_complete_with_an_outcome` and `test_village_flow_kills_and_announces_a_second_victim_after_voting` have a comment above their `task.cancel()` reading:

```python
        # The flow loops into the next night/day forever from here (no win
        # condition yet), so cancel it instead of awaiting completion.
```

Replace both occurrences with:

```python
        # The default 7-player roster needs 3 kills to reach werewolf
        # parity, so the game genuinely isn't over yet at this point --
        # cancel rather than waiting for a natural end this test won't reach.
```

In `tests/test_concurrency.py`, `_run_one_session` has the same comment above its `flow_task.cancel()`:

```python
    # The flow loops into the next night/day forever from here (no win
    # condition yet), so cancel it instead of awaiting completion.
```

Replace with:

```python
    # This test only plays to the first VOTING_COMPLETE -- the default
    # 7-player roster needs 3 kills to reach werewolf parity, so the game
    # genuinely isn't over yet; cancel rather than waiting for a natural end.
```

- [ ] **Step 6: Run the full test file suite once more**

Run: `pytest tests/test_flow.py tests/test_concurrency.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/the_village/village_flow.py tests/test_flow.py tests/test_concurrency.py
git commit -m "feat: end VillageFlow with a GameOverResult when a winner is decided"
```

---

## Task 4: `ui.py` formatting helpers — completed history + game-over message

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `GameOverResult` (Task 2); `_speaker_color_index`, `VILLAGER_CHIP_CLASS`, `CHIP_LIST_CLASS`, `_dead_days` (existing).
- Produces: `format_completed_round_history(state, include_current_day: bool = False) -> str` (new optional parameter, default-compatible with all existing callers); `format_game_over(result: GameOverResult, state: GameState) -> str` (new function).

- [ ] **Step 1: Write the failing tests**

Add `GameOverResult` to the `the_village.bridge` import line in `tests/test_ui.py`:

```python
from the_village.bridge import FlowFailed, FlowStatus, GameOverResult, PlayerInput, SessionBridge
```

Add `format_game_over` to the `the_village.ui` import block (alphabetically, after `format_discussion_transcript`):

```python
from the_village.ui import (
    begin_discussion,
    cast_player_abstain,
    cast_player_vote,
    format_alive_panel,
    format_completed_round_history,
    format_deaths_panel,
    format_discussion_transcript,
    format_game_over,
    format_latest_death_announcement,
    format_lynched_panel,
    format_vote_result,
    pass_discussion_turn,
    send_discussion_turn,
    start_game,
    start_voting,
)
```

Append these tests (near the existing `format_completed_round_history` tests, e.g. after `test_format_completed_round_history_includes_day_ones_own_death`):

```python
def test_format_completed_round_history_excludes_current_day_by_default():
    state = GameState(
        user_player_name="Dana",
        players=[Player(name="Dana", player_type="user")],
        days=[Day(day_number=1, player_found_dead="A")],
    )
    assert format_completed_round_history(state) == ""


def test_format_completed_round_history_includes_the_current_day_when_requested():
    state = GameState(
        user_player_name="Dana",
        players=[Player(name="Dana", player_type="user")],
        days=[Day(day_number=1, player_found_dead="A")],
    )
    rendered = format_completed_round_history(state, include_current_day=True)
    assert "Monday" in rendered
    assert "A" in rendered


def test_format_game_over_announces_villagers_win():
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user"),
            Player(name="W", player_type="werewolf", is_alive=False),
        ],
    )
    result = GameOverResult(winner="villagers", werewolf_names=["W"])
    rendered = format_game_over(result, state)
    assert "The Villagers Win!" in rendered
    assert ">W</span>" in rendered


def test_format_game_over_announces_werewolves_win():
    state = GameState(
        user_player_name="Dana",
        players=[Player(name="Dana", player_type="user")],
    )
    result = GameOverResult(winner="werewolves", werewolf_names=[])
    rendered = format_game_over(result, state)
    assert "The Werewolves Win!" in rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -k "format_completed_round_history_includes_the_current_day or format_game_over" -v`
Expected: FAIL — `TypeError` (unexpected keyword `include_current_day`) and `ImportError` (`format_game_over` doesn't exist).

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`, add the `GameOverResult` import:

```python
from the_village.bridge import (
    FlowFailed,
    FlowStatus,
    GameOverResult,
    PlayerInput,
    SessionBridge,
    run_flow,
)
```

`format_completed_round_history` currently starts like this (its signature, its existing comment block, and the first two lines of its body):

```python
def format_completed_round_history(state: GameState) -> str:
    # state.current_day (the live, still-in-progress round) is excluded --
    # its discussion/vote widgets render that content live elsewhere on
    # the page. Every prior day is fully resolved, so it's folded in here
    # once and for all rather than staying in the live widgets, which get
    # reused (and reset) for each new round.
    #
    # Rendered oldest-first (chronological), each day in its own panel --
    # newly completed days append at the bottom of the stack rather than
    # appearing directly under the live card.
    dead_day_index = {id(day): index for index, day in enumerate(_dead_days(state))}
    completed_days = state.days[:-1]
```

Change it to:

```python
def format_completed_round_history(
    state: GameState, include_current_day: bool = False
) -> str:
    # state.current_day (the live, still-in-progress round) is excluded by
    # default -- its discussion/vote widgets render that content live
    # elsewhere on the page. Every prior day is fully resolved, so it's
    # folded in here once and for all rather than staying in the live
    # widgets, which get reused (and reset) for each new round.
    # include_current_day=True overrides that for the game-over panel,
    # whose final round never advances into a new Day (see
    # VillageFlow.check_winner_after_lynching) and so would otherwise never
    # get folded into the permanent record at all.
    #
    # Rendered oldest-first (chronological), each day in its own panel --
    # newly completed days append at the bottom of the stack rather than
    # appearing directly under the live card.
    dead_day_index = {id(day): index for index, day in enumerate(_dead_days(state))}
    completed_days = state.days if include_current_day else state.days[:-1]
```

The rest of the function body (the `for day in completed_days:` loop and everything after) is unchanged.

Add `format_game_over` right after `format_vote_result`:

```python
def format_game_over(result: GameOverResult, state: GameState) -> str:
    headline = (
        "The Villagers Win!" if result.winner == "villagers" else "The Werewolves Win!"
    )
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS}" '
        f'style="color: var(--speaker-{_speaker_color_index(name, state)})">{name}</span>'
        for name in result.werewolf_names
    )
    return (
        f"### {headline}\n\n"
        f'The werewolves were: <div class="{CHIP_LIST_CLASS}">{chips}</div>'
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -v`
Expected: PASS (all tests, old and new — the new `include_current_day` parameter defaults to `False`, so every existing `format_completed_round_history(state)` call keeps its old behavior).

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: add game-over formatting helpers to ui.py"
```

---

## Task 5: Results panel — kill-triggered path (`_stream_bridge`, `begin_discussion`, `send_discussion_turn`, `pass_discussion_turn`)

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `format_completed_round_history(..., include_current_day=True)`, `format_game_over` (Task 4); `GameOverResult` (Task 2).
- Produces: new Gradio components `game_over_panel` (a `gr.Column`, hidden by default), `game_over_status` (a `gr.Markdown` inside it), and `play_again_button` (a `gr.Button` inside it, wired up in Task 8). `discussion_outputs` grows from 7 to 10 slots (adds `history_log`, `game_over_panel`, `game_over_status`), so every generator bound to it now yields 10-tuples.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py` (near the other `begin_discussion` tests, e.g. after `test_begin_discussion_raises_gr_error_on_flow_failed`):

```python
async def test_begin_discussion_shows_the_results_panel_on_game_over():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    result = GameOverResult(winner="werewolves", werewolf_names=["A"])
    await bridge.outbox.put(result)

    state = _discussion_state()
    outputs = [update async for update in begin_discussion(bridge, state)]

    assert await waiter == PlayerInput()
    (
        _bridge,
        _discussion_transcript,
        _discussion_textbox,
        discussion_input_row,
        discussion_status,
        begin_discussion_button,
        panel_death_line,
        history_log,
        game_over_panel,
        game_over_status,
    ) = outputs[-1]
    assert discussion_input_row["visible"] is False
    assert discussion_status["visible"] is False
    assert begin_discussion_button["visible"] is False
    assert panel_death_line["visible"] is False
    assert "A" in history_log["value"]
    assert game_over_panel["visible"] is True
    assert "The Werewolves Win!" in game_over_status["value"]
```

Also fix the one existing test that hardcodes the old 7-item width — in `test_send_discussion_turn_is_a_noop_on_blank_message`, change:

```python
    assert outputs == [(gr.skip(),) * 7]
```

to:

```python
    assert outputs == [(gr.skip(),) * 10]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -k "begin_discussion or send_discussion_turn" -v`
Expected: FAIL — `test_begin_discussion_shows_the_results_panel_on_game_over` fails to unpack (still 7-tuples), and `test_send_discussion_turn_is_a_noop_on_blank_message` fails the length assertion.

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`'s `build_app`, add the new components right after the `LIVE_DAY_CARD_CLASS` column closes (still nested inside the same outer `with gr.Column():` that holds `history_log`, so the panel sits alongside it, not inside it):

```python
                with gr.Column(
                    visible=False, elem_classes=[DAY_PANEL_CLASS]
                ) as game_over_panel:
                    game_over_status = gr.Markdown()
                    play_again_button = gr.Button("Play Again")
```

Update `discussion_outputs`:

```python
        discussion_outputs = [
            session_bridge,
            discussion_transcript,
            discussion_textbox,
            discussion_input_row,
            discussion_status,
            begin_discussion_button,
            panel_death_line,
            history_log,
            game_over_panel,
            game_over_status,
        ]
```

Replace `_stream_bridge` in full:

```python
async def _stream_bridge(bridge: SessionBridge, state: GameState):
    """Drains bridge.outbox, yielding a UI-update tuple per item, until a
    status tells the caller to stop and hand control back to the player."""
    try:
        while True:
            if bridge.outbox.empty():
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages, waiting=True
                    ),
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            item = await bridge.outbox.get()
            logger.debug("_stream_bridge: bridge=%s got item=%r", id(bridge), item)
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, GameOverResult):
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages
                    ),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(
                        value=format_completed_round_history(
                            state, include_current_day=True
                        )
                    ),
                    gr.update(visible=True),
                    gr.update(value=format_game_over(item, state)),
                )
                return
            if isinstance(item, DiscussionMessage):
                if item.player_name != state.user_player_name:
                    pending_transcript = format_discussion_transcript(
                        state,
                        limit=bridge.revealed_discussion_messages,
                        pending_speaker=item.player_name,
                    )
                    yield (
                        bridge,
                        pending_transcript,
                        gr.update(value=""),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                    )
                    await asyncio.sleep(SPEAKER_THINKING_DELAY_SECONDS)
                bridge.revealed_discussion_messages += 1
                transcript = format_discussion_transcript(
                    state, limit=bridge.revealed_discussion_messages
                )
                yield (
                    bridge,
                    transcript,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            elif (
                item == FlowStatus.WAITING_FOR_TURN
                or item == FlowStatus.WAITING_FOR_ANSWER
            ):
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages
                    ),
                    gr.update(value=""),
                    gr.update(visible=True),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
                return
            elif item == FlowStatus.DISCUSSION_COMPLETE:
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages
                    ),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(
                        visible=True,
                        value=_discussion_complete_notice(state),
                    ),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc
```

Update `begin_discussion`'s yield (add 3 trailing `gr.update()`):

```python
async def begin_discussion(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput()):
        return  # already resolved (e.g. double-click) -- no-op
    bridge.revealed_discussion_messages = 0
    bridge.voting_started = False
    yield (
        bridge,
        format_discussion_transcript(
            state, limit=bridge.revealed_discussion_messages, waiting=True
        ),
        gr.update(
            label=f"{state.user_player_name}, please say something "
            "(unless you have nothing to say)"
        ),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(value=format_latest_death_announcement(state), visible=True),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update
```

Update `send_discussion_turn`:

```python
async def send_discussion_turn(bridge: SessionBridge, state: GameState, message: str):
    if not message.strip():
        yield (gr.skip(),) * 10
        return
    if not bridge.resolve_input(PlayerInput(text=message.strip())):
        return
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update
```

Update `pass_discussion_turn`:

```python
async def pass_discussion_turn(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput(text=None)):
        return
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -v`
Expected: PASS. If any test that indexes into the tuple by fixed position (e.g. `outputs[0][4]["value"]` in `test_discussion_complete_status_value_differs_across_rounds`) fails, it's because that index still points at `discussion_status` (position 4) unchanged by this task's trailing additions — a failure there signals a real mistake in the new tuples' ordering, not a test that needs updating.

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: show the results panel when a night kill ends the game"
```

---

## Task 6: Results panel — lynch-triggered path when the human is already dead (`start_voting`)

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: same as Task 5.
- Produces: `_next_day_setup(bridge, state) -> _NextDayPanel | GameOverResult` (return type widened). `start_voting`'s outputs list grows from `11 + MAX_VOTE_CANDIDATES` to `13 + MAX_VOTE_CANDIDATES` (adds `game_over_panel`, `game_over_status`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
async def test_start_voting_shows_the_results_panel_when_the_lynch_ends_the_game():
    # Happens when the human is already dead (same no-ballot path
    # test_start_voting_advances_to_next_day_on_voting_complete_instead_of_hanging
    # covers) and the lynch that just completed also ends the game -- there's
    # no next night's death to drain, only a GameOverResult.
    state = GameState(
        user_player_name="Dana",
        players=[
            Player(name="Dana", player_type="user", is_alive=False),
            Player(name="W", player_type="werewolf", is_alive=False),
        ],
        days=[Day(day_number=1, player_lynched="W")],
    )
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)
    await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
    result = GameOverResult(winner="villagers", werewolf_names=["W"])
    await bridge.outbox.put(result)

    outputs = [update async for update in start_voting(bridge, state)]

    assert await waiter == PlayerInput()
    assert len(outputs) == 2  # heartbeat, then the results panel
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        vote_status_update,
        discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        _discussion_title_update,
        history_log_update,
        _discussion_transcript_update,
        panel_death_line_update,
        game_over_panel_update,
        game_over_status_update,
    ) = outputs[-1]
    assert vote_status_update["visible"] is False
    assert discussion_status_update["visible"] is False
    assert begin_button_update["visible"] is False
    assert panel_death_line_update["visible"] is False
    assert "W" in history_log_update["value"]
    assert game_over_panel_update["visible"] is True
    assert "The Villagers Win!" in game_over_status_update["value"]
```

Update the three existing `start_voting` tests that fully unpack its output tuple — add two more trailing names, asserted as untouched no-ops in each:

In `test_start_voting_resolves_the_discussion_gate_and_reveals_the_ballot`, change the unpack:

```python
    (
        _bridge,
        row_update,
        *candidate_updates,
        status_update,
        discussion_status_update,
        alive_panel_update,
        deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
    ) = outputs[0]
```

to:

```python
    (
        _bridge,
        row_update,
        *candidate_updates,
        status_update,
        discussion_status_update,
        alive_panel_update,
        deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
        game_over_panel_update,
        game_over_status_update,
    ) = outputs[0]
```

and add, after the existing `assert panel_death_line_update == gr.update()`:

```python
    assert game_over_panel_update == gr.update()
    assert game_over_status_update == gr.update()
```

In `test_start_voting_advances_to_next_day_on_voting_complete_instead_of_hanging`, change its `outputs[1]` unpack:

```python
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        vote_status_update,
        discussion_status_update,
        alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        _history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
    ) = outputs[1]
```

to:

```python
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        vote_status_update,
        discussion_status_update,
        alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        _history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
        game_over_panel_update,
        game_over_status_update,
    ) = outputs[1]
```

and after the existing `assert panel_death_line_update["visible"] is False`, add:

```python
    assert game_over_panel_update == gr.update()
    assert game_over_status_update == gr.update()
```

In `test_start_voting_renders_a_vote_outcome_before_voting_complete`, change its first unpack (of `outputs[0]`):

```python
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        status_update,
        discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        _begin_button_update,
        _discussion_title_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
    ) = outputs[0]
```

to:

```python
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        status_update,
        discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        _begin_button_update,
        _discussion_title_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
        _game_over_panel_update,
        _game_over_status_update,
    ) = outputs[0]
```

and its second unpack (of `outputs[2]`):

```python
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        _status_update,
        _discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
    ) = outputs[2]
```

to:

```python
    (
        _bridge,
        _row_update,
        *_candidate_updates,
        _status_update,
        _discussion_status_update,
        _alive_panel_update,
        _deaths_panel_update,
        begin_button_update,
        discussion_title_update,
        _history_log_update,
        _discussion_transcript_update,
        _panel_death_line_update,
        _game_over_panel_update,
        _game_over_status_update,
    ) = outputs[2]
```

In `test_start_voting_second_invocation_does_not_resolve_a_later_pending_input`, change:

```python
    assert second_outputs[0] == (gr.update(),) * (11 + ui.MAX_VOTE_CANDIDATES)
```

to:

```python
    assert second_outputs[0] == (gr.update(),) * (13 + ui.MAX_VOTE_CANDIDATES)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -k start_voting -v`
Expected: FAIL — unpacking errors (wrong tuple width) and the new game-over test not finding a matching branch.

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`, widen `_next_day_setup`'s return type and behavior:

```python
async def _next_day_setup(
    bridge: SessionBridge, state: GameState
) -> _NextDayPanel | GameOverResult:
    # VillageFlow.run_next_night routes straight into the next night and
    # re-arms announce_death, so the very next outbox item is normally the
    # following morning's death announcement (a bare str) -- the same shape
    # start_game waits on for night one. But if check_winner_after_lynching
    # decided the game just ended instead, run_next_night never runs, and
    # this is a GameOverResult instead. Draining it here (without resolving
    # anything) lets the flow keep running in the background while
    # announce_death stays paused at its own wait_for_input() -- resolving
    # that pause is begin_discussion_button.click's job, same as day one, so
    # every day gates its death reveal and discussion behind the same
    # "Begin" click.
    item = await bridge.outbox.get()
    if isinstance(item, FlowFailed):
        raise gr.Error("Something went wrong, please try again.")
    if isinstance(item, GameOverResult):
        return item
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    return _NextDayPanel(
        alive_panel=format_alive_panel(state),
        deaths_panel=format_deaths_panel(state),
        discussion_title=gr.update(value=f"### {weekday}", visible=True),
        history_log=gr.update(value=format_completed_round_history(state)),
    )
```

Update the `discussion_status.change` outputs list in `build_app`:

```python
        discussion_status.change(
            fn=start_voting,
            inputs=[session_bridge, game_state],
            outputs=[
                session_bridge,
                vote_button_row,
                *candidate_buttons,
                vote_status,
                discussion_status,
                alive_panel,
                deaths_panel,
                begin_discussion_button,
                discussion_title,
                history_log,
                discussion_transcript,
                panel_death_line,
                game_over_panel,
                game_over_status,
            ],
        )
```

In `start_voting`, update the no-op guard width:

```python
    if bridge.voting_started:
        yield (gr.update(),) * (13 + MAX_VOTE_CANDIDATES)
        return
```

Add two trailing `gr.update()` to the `WAITING_FOR_VOTE` yield:

```python
            if item == FlowStatus.WAITING_FOR_VOTE:
                yield (
                    bridge,
                    gr.update(visible=True),
                    *_vote_button_updates(state),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
                return
```

Add two trailing `gr.update()` to the `VoteOutcome` yield:

```python
            elif isinstance(item, VoteOutcome):
                yield (
                    bridge,
                    gr.update(),
                    *([gr.update()] * MAX_VOTE_CANDIDATES),
                    gr.update(value=format_vote_result(state, item), visible=True),
                    gr.update(value=_voting_results_notice(state)),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
```

Replace the `VOTING_COMPLETE` branch entirely:

```python
            elif item == FlowStatus.VOTING_COMPLETE:
                # cast_player_vote is what normally advances into the next
                # day's Begin-gated panel on VOTING_COMPLETE, but it never
                # runs in the human-is-dead path above -- do it here instead,
                # or the round has no way forward. See _next_day_setup.
                # _next_day_setup then blocks on the werewolf pack's
                # kill-selection LLM call (via bridge.outbox.get()), which
                # can take several seconds with nothing yielded in between --
                # yield a no-op heartbeat first so the frontend has a fresh
                # update to hold onto rather than sitting on a stale pending
                # state that long.
                yield (bridge,) + (gr.update(),) * 18
                next_step = await _next_day_setup(bridge, state)
                if isinstance(next_step, GameOverResult):
                    yield (
                        bridge,
                        gr.update(),
                        *([gr.update()] * MAX_VOTE_CANDIDATES),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(
                            value=format_completed_round_history(
                                state, include_current_day=True
                            )
                        ),
                        gr.update(),
                        gr.update(),
                        gr.update(visible=True),
                        gr.update(value=format_game_over(next_step, state)),
                    )
                    return
                yield (
                    bridge,
                    gr.update(),
                    *([gr.update()] * MAX_VOTE_CANDIDATES),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    next_step.alive_panel,
                    next_step.deaths_panel,
                    # Gradio unmounts a hidden component entirely (see the
                    # comment on discussion_input_row in _autofocus_js), so
                    # its label has to be resent here, not just `visible`,
                    # or the remounted button comes back blank.
                    gr.update(value="Begin", visible=True),
                    next_step.discussion_title,
                    next_step.history_log,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                )
                return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: show the results panel when a lynch ends the game (human already dead)"
```

---

## Task 7: Results panel — lynch-triggered path when the human votes (`cast_player_vote`)

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `_next_day_setup` returning `_NextDayPanel | GameOverResult` (Task 6); `format_game_over`, `format_completed_round_history(..., include_current_day=True)` (Task 4).
- Produces: `vote_outputs` grows from 11 to 13 slots (adds `game_over_panel`, `game_over_status`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
async def test_cast_player_vote_shows_the_results_panel_when_the_lynch_ends_the_game():
    state = _voting_state()
    state.players.append(Player(name="W", player_type="werewolf"))
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    outcome = VoteOutcome(day_number=2, votes=[], tally={"W": 1}, lynched="W")

    async def feed_outcome_and_game_over():
        next(p for p in state.players if p.name == "W").is_alive = False
        state.current_day.player_lynched = "W"
        await bridge.outbox.put(outcome)
        await bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
        await bridge.outbox.put(GameOverResult(winner="villagers", werewolf_names=["W"]))

    events = cast_player_vote(bridge, state, "W")
    await events.__anext__()  # the "Tallying..." yield; also resolves waiter
    await waiter
    await feed_outcome_and_game_over()
    await events.__anext__()  # the VoteOutcome yield
    await events.__anext__()  # the no-op heartbeat yield

    (
        row_update,
        status_update,
        _alive_panel_value,
        _lynched_panel_value,
        _deaths_panel_value,
        begin_button_update,
        _discussion_title_update,
        discussion_status_update,
        history_log_update,
        _discussion_transcript_update,
        panel_death_line_update,
        game_over_panel_update,
        game_over_status_update,
    ) = await events.__anext__()

    assert row_update["visible"] is False
    assert status_update["visible"] is False
    assert begin_button_update["visible"] is False
    assert discussion_status_update["visible"] is False
    assert panel_death_line_update["visible"] is False
    assert "W" in history_log_update["value"]
    assert game_over_panel_update["visible"] is True
    assert "The Villagers Win!" in game_over_status_update["value"]

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()
```

Update the one existing test that fully unpacks and asserts the next-day continuation tuple — `test_cast_player_vote_advances_to_next_days_begin_gated_panel_on_voting_complete`. Change its unpack:

```python
    (
        _row_update,
        status_update,
        alive_panel_value,
        _lynched_panel_value,
        deaths_panel_value,
        begin_discussion_update,
        discussion_title_update,
        discussion_status_update,
        _history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
    ) = await events.__anext__()
```

to:

```python
    (
        _row_update,
        status_update,
        alive_panel_value,
        _lynched_panel_value,
        deaths_panel_value,
        begin_discussion_update,
        discussion_title_update,
        discussion_status_update,
        _history_log_update,
        discussion_transcript_update,
        panel_death_line_update,
        game_over_panel_update,
        game_over_status_update,
    ) = await events.__anext__()
```

and add, after the existing `assert status_update["visible"] is False`:

```python
    assert game_over_panel_update == gr.update()
    assert game_over_status_update == gr.update()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -k cast_player_vote -v`
Expected: FAIL — unpacking errors and the new game-over test finding no matching branch (it falls through to the unchanged next-day continuation, whose `_next_day_setup` call now returns a `GameOverResult` it doesn't yet handle).

- [ ] **Step 3: Implement**

Update `vote_outputs` in `build_app`:

```python
        vote_outputs = [
            vote_button_row,
            vote_status,
            alive_panel,
            lynched_panel,
            deaths_panel,
            begin_discussion_button,
            discussion_title,
            discussion_status,
            history_log,
            discussion_transcript,
            panel_death_line,
            game_over_panel,
            game_over_status,
        ]
```

In `cast_player_vote`, add two trailing `gr.update()` to the initial "Tallying…" yield:

```python
    yield (
        gr.update(visible=False),
        gr.update(value="Tallying the votes…", visible=True),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(value=_voting_results_notice(state)),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
```

Add two trailing `gr.update()` to the `VoteOutcome` yield:

```python
            if isinstance(item, VoteOutcome):
                yield (
                    gr.update(),
                    gr.update(value=format_vote_result(state, item), visible=True),
                    format_alive_panel(state),
                    format_lynched_panel(state),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(value=_voting_results_notice(state)),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
```

Replace the `VOTING_COMPLETE` branch:

```python
            elif item == FlowStatus.VOTING_COMPLETE:
                # The round auto-advances into the next day's panel as soon
                # as the outcome is known, in the same Begin-gated state day
                # one starts in -- see _next_day_setup. _next_day_setup then
                # blocks on the werewolf pack's kill-selection LLM call
                # (via bridge.outbox.get()), which can take several seconds
                # with nothing yielded in between -- yield a no-op heartbeat
                # first so the frontend has a fresh update to hold onto
                # rather than sitting on a stale pending state that long.
                yield (gr.update(),) * 13
                next_step = await _next_day_setup(bridge, state)
                if isinstance(next_step, GameOverResult):
                    yield (
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(visible=False),
                        gr.update(
                            value=format_completed_round_history(
                                state, include_current_day=True
                            )
                        ),
                        gr.update(),
                        gr.update(visible=False),
                        gr.update(visible=True),
                        gr.update(value=format_game_over(next_step, state)),
                    )
                    return
                yield (
                    gr.update(),
                    gr.update(visible=False),
                    next_step.alive_panel,
                    gr.update(),
                    next_step.deaths_panel,
                    # Gradio unmounts a hidden component entirely (see the
                    # comment on discussion_input_row in _autofocus_js), so
                    # its label has to be resent here, not just `visible`,
                    # or the remounted button comes back blank.
                    gr.update(value="Begin", visible=True),
                    next_step.discussion_title,
                    gr.update(visible=False),
                    next_step.history_log,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                )
                return
```

Add the `GameOverResult` import if not already present from an earlier task (it is, from Task 5/6 — no change needed here).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: show the results panel when a lynch ends the game (human voted)"
```

---

## Task 8: "Play Again"

**Files:**
- Modify: `src/the_village/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `start_game` (existing, unchanged signature); `game_over_panel`, `game_over_status`, `play_again_button` (Task 5).
- Produces: `play_again(state: GameState)` async generator (new); `start_game`'s outputs list grows to also reset the previous game's leftover UI state.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
async def test_play_again_starts_a_new_game_with_the_same_player_name():
    state = GameState(user_player_name="TestPlayer")

    outputs = [update async for update in ui.play_again(state)]

    assert len(outputs) == 1
    new_state = outputs[0][6]
    assert isinstance(new_state, GameState)
    assert new_state.user_player_name == "TestPlayer"
    assert new_state is not state
```

Update `test_start_game_yields_once_paused_at_the_death_gate` to also check the new reset slots. Change:

```python
async def test_start_game_yields_once_paused_at_the_death_gate():
    outputs = [update async for update in start_game("TestPlayer")]

    assert len(outputs) == 1
    bridge = outputs[0][7]
    assert isinstance(bridge, SessionBridge)
    assert bridge.pending_input is not None
```

to:

```python
async def test_start_game_yields_once_paused_at_the_death_gate():
    outputs = [update async for update in start_game("TestPlayer")]

    assert len(outputs) == 1
    bridge = outputs[0][7]
    assert isinstance(bridge, SessionBridge)
    assert bridge.pending_input is not None
    assert outputs[0][8]["visible"] is False  # game_over_panel starts hidden
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui.py -k "play_again or start_game_yields" -v`
Expected: FAIL — `AttributeError: module 'the_village.ui' has no attribute 'play_again'`, and an index error on `outputs[0][8]` (tuple is still 8 items).

- [ ] **Step 3: Implement**

In `src/the_village/ui.py`, update `start_game`'s final yield to also reset every other live-game component back to its fresh-game state (needed so "Play Again" doesn't leave the previous game's history/discussion/vote widgets showing):

```python
    state = village_flow.state
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    yield (
        gr.update(visible=False),
        gr.update(visible=True),
        format_deaths_panel(state),
        format_alive_panel(state),
        format_lynched_panel(state),
        gr.update(value=f"### {weekday}", visible=True),
        state,
        bridge,
        gr.update(visible=False),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(visible=False),
        gr.update(value="Begin", visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
    )


async def play_again(state: GameState):
    async for update in start_game(state.user_player_name):
        yield update
```

In `build_app`, define a shared outputs list and use it for both bindings. Replace the `start_button.click(...)` call:

```python
        start_game_outputs = [
            start_screen,
            result_screen,
            deaths_panel,
            alive_panel,
            lynched_panel,
            discussion_title,
            game_state,
            session_bridge,
            game_over_panel,
            game_over_status,
            history_log,
            discussion_transcript,
            panel_death_line,
            begin_discussion_button,
            discussion_status,
            vote_button_row,
            vote_status,
        ]

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=start_game_outputs,
            concurrency_limit=None,
        )
```

Add the new binding right after it:

```python
        play_again_button.click(
            fn=play_again,
            inputs=[game_state],
            outputs=start_game_outputs,
            concurrency_limit=None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS (every test in the repo)

- [ ] **Step 6: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py
git commit -m "feat: add Play Again to the results panel"
```

---

## Manual verification

After Task 8, in the browser (`gradio src/the_village/ui.py` or however this project normally launches the Gradio app):

1. Play a full game to a villager win (vote out both werewolves) — confirm the results panel shows "The Villagers Win!" alongside the history log (not replacing it), lists the correct werewolf names, and "Play Again" starts a fresh game with a new roster.
2. Play (or force, by declining/abstaining through several nights) to a werewolf win via night kills — confirm the panel appears right after a "Begin" click reveals the deciding death, per the confirmed UX.
3. Confirm `crewai run` (the CLI smoke test) completes on its own within a few seconds instead of needing a manual timeout/cancel.
