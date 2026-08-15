# Werewolf Night-One Meeting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a player start a game (entering their first name in a Gradio UI), have a 7-villager roster (1 player + 6 AI villagers, 2 of them secretly werewolves) generated, resolve the werewolves' first-night kill as a random pick among the non-werewolf AI villagers, and show the player a two-column result screen (event log + running list of werewolf kills).

**Architecture:** A `CrewAI Flow` (`VillageFlow`) holds a typed `GameState` (Pydantic). `@start()` builds the roster and assigns werewolves/pack-leader; `@listen()` resolves the night-one kill. Both steps are plain deterministic Python — no `Agent`/`Task`/`Crew` is used, since night one has no information for werewolves to reason about (see spec's "Why no CrewAI Crew" section). A Gradio `Blocks` app wraps `VillageFlow().kickoff(...)` per session, using `gr.State` so concurrent users' games never share state.

**Tech Stack:** Python 3.13, `crewai` (Flow only, no Agent/Crew), Pydantic (via crewai), `gradio`, `pytest`, `uv` for dependency management.

**Spec:** `docs/superpowers/specs/2026-08-14-werewolf-night-one-design.md`

## Global Constraints

- Roster is fixed at 7 villagers: 1 `player_type="user"` + 6 `player_type="villager"`, of which exactly 2 are reassigned `player_type="werewolf"`, one of those flagged `is_pack_leader=True`. Not configurable via UI.
- AI villager names are sampled from the fixed name pool, excluding (case-insensitive) any pool entry matching the player's name — no AI villager may share the player's name.
- `day_number` starts at `1` (Sunday). Night one's kill is revealed as `day_number=2` ("Monday").
- The player (`player_type="user"`) is never eligible to be killed on night one — eligible targets are always `player_type == "villager" and is_alive`.
- No LLM/Crew/Agent/Task is used for the night-one kill decision — it is `random.choice` over eligible targets, done in plain code.
- Each Gradio browser session gets its own `gr.State`-backed `VillageFlow` run; no shared/global mutable game state.
- Never use YAML files for CrewAI config (per `AGENTS.md`) — not triggered here since no `Agent`/`Crew` config is created, but stays true for any future extension of this code.

---

### Task 1: Core data model + pytest setup

**Files:**
- Create: `src/the_village/state.py`
- Modify: `pyproject.toml` (add `pytest` dev dependency, add `[tool.pytest.ini_options]`)
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `PlayerType` (`Literal["user", "villager", "werewolf"]`), `WEEKDAYS: list[str]`, `Villager(BaseModel)` with fields `name: str`, `player_type: PlayerType`, `is_pack_leader: bool = False`, `is_alive: bool = True`; `Death(BaseModel)` with fields `name: str`, `day_number: int`; `GameState(BaseModel)` with fields `player_name: str = ""`, `day_number: int = 1`, `villagers: list[Villager] = []`, `deaths: list[Death] = []`.

- [ ] **Step 1: Add pytest as a dev dependency**

Run: `uv add --dev pytest`

- [ ] **Step 2: Configure pytest**

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_state.py`:

```python
from the_village.state import WEEKDAYS, Death, GameState, Villager


def test_villager_defaults():
    villager = Villager(name="Alice", player_type="villager")
    assert villager.is_pack_leader is False
    assert villager.is_alive is True


def test_death_fields():
    death = Death(name="Alice", day_number=2)
    assert death.name == "Alice"
    assert death.day_number == 2


def test_game_state_defaults():
    state = GameState()
    assert state.player_name == ""
    assert state.day_number == 1
    assert state.villagers == []
    assert state.deaths == []


def test_weekdays_starts_on_sunday():
    assert WEEKDAYS[0] == "Sunday"
    assert len(WEEKDAYS) == 7
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.state'`

- [ ] **Step 5: Write the implementation**

Create `src/the_village/state.py`:

```python
from typing import Literal

from pydantic import BaseModel

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


class Villager(BaseModel):
    name: str
    player_type: PlayerType
    is_pack_leader: bool = False
    is_alive: bool = True


class Death(BaseModel):
    name: str
    day_number: int


class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1
    villagers: list[Villager] = []
    deaths: list[Death] = []
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/the_village/state.py tests/test_state.py
git commit -m "feat: add GameState data model and pytest setup"
```

---

### Task 2: Roster setup logic

**Files:**
- Create: `src/the_village/roster.py`
- Test: `tests/test_roster.py`

**Interfaces:**
- Consumes: `GameState`, `Villager` from `src/the_village/state.py` (Task 1).
- Produces: `VILLAGER_NAME_POOL: list[str]` (12 names); `build_initial_roster(player_name: str, rng: random.Random | None = None) -> GameState`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_roster.py`:

```python
import random

from the_village.roster import VILLAGER_NAME_POOL, build_initial_roster


def test_roster_has_seven_villagers():
    state = build_initial_roster("Dana", random.Random(1))
    assert len(state.villagers) == 7


def test_roster_has_one_user_two_werewolves_four_villagers():
    state = build_initial_roster("Dana", random.Random(1))
    by_type = {"user": 0, "werewolf": 0, "villager": 0}
    for villager in state.villagers:
        by_type[villager.player_type] += 1
    assert by_type == {"user": 1, "werewolf": 2, "villager": 4}


def test_player_is_first_villager_and_is_user_type():
    state = build_initial_roster("Dana", random.Random(1))
    assert state.villagers[0].name == "Dana"
    assert state.villagers[0].player_type == "user"


def test_exactly_one_pack_leader_among_werewolves():
    state = build_initial_roster("Dana", random.Random(1))
    werewolves = [v for v in state.villagers if v.player_type == "werewolf"]
    leaders = [v for v in werewolves if v.is_pack_leader]
    assert len(leaders) == 1


def test_ai_villager_names_come_from_pool_and_are_unique():
    state = build_initial_roster("Dana", random.Random(1))
    ai_names = [v.name for v in state.villagers if v.player_type != "user"]
    assert len(ai_names) == len(set(ai_names))
    assert all(name in VILLAGER_NAME_POOL for name in ai_names)


def test_ai_villager_names_exclude_a_player_name_matching_the_pool():
    state = build_initial_roster("Alice", random.Random(1))
    ai_names = [v.name for v in state.villagers if v.player_type != "user"]
    assert "Alice" not in ai_names


def test_ai_villager_names_exclude_a_player_name_case_insensitively():
    state = build_initial_roster("alice", random.Random(1))
    ai_names = [v.name for v in state.villagers if v.player_type != "user"]
    assert "Alice" not in ai_names


def test_day_number_starts_at_one():
    state = build_initial_roster("Dana", random.Random(1))
    assert state.day_number == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.roster'`

- [ ] **Step 3: Write the implementation**

Create `src/the_village/roster.py`:

```python
import random

from the_village.state import GameState, Villager

VILLAGER_NAME_POOL = [
    "Alice",
    "Bram",
    "Corin",
    "Della",
    "Edwin",
    "Fiora",
    "Garrick",
    "Hattie",
    "Ilsa",
    "Jorah",
    "Kestrel",
    "Lior",
]


def build_initial_roster(
    player_name: str, rng: random.Random | None = None
) -> GameState:
    rng = rng or random.Random()

    available_names = [
        name
        for name in VILLAGER_NAME_POOL
        if name.lower() != player_name.strip().lower()
    ]
    ai_names = rng.sample(available_names, 6)
    villagers = [Villager(name=player_name, player_type="user")]
    villagers += [
        Villager(name=name, player_type="villager") for name in ai_names
    ]

    werewolves = rng.sample(villagers[1:], 2)
    for werewolf in werewolves:
        werewolf.player_type = "werewolf"
    rng.choice(werewolves).is_pack_leader = True

    return GameState(player_name=player_name, day_number=1, villagers=villagers)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roster.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/roster.py tests/test_roster.py
git commit -m "feat: add roster setup and werewolf assignment logic"
```

---

### Task 3: Night-one kill logic

**Files:**
- Create: `src/the_village/night.py`
- Test: `tests/test_night.py`

**Interfaces:**
- Consumes: `GameState`, `Villager`, `Death` from `src/the_village/state.py` (Task 1).
- Produces: `resolve_night_one(state: GameState, rng: random.Random | None = None) -> GameState` (mutates and returns `state`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_night.py`:

```python
import random

from the_village.night import resolve_night_one
from the_village.state import GameState, Villager


def make_state() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="D", player_type="villager"),
        Villager(name="E", player_type="werewolf", is_pack_leader=True),
        Villager(name="F", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=1, villagers=villagers)


def test_kills_a_non_player_non_werewolf_villager():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert len(state.deaths) == 1
    killed_name = state.deaths[0].name
    assert killed_name in {"A", "B", "C", "D"}


def test_killed_villager_marked_not_alive():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    killed_name = state.deaths[0].name
    killed = next(v for v in state.villagers if v.name == killed_name)
    assert killed.is_alive is False


def test_player_and_werewolves_survive_night_one():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    player = next(v for v in state.villagers if v.player_type == "user")
    werewolves = [v for v in state.villagers if v.player_type == "werewolf"]
    assert player.is_alive is True
    assert all(w.is_alive for w in werewolves)


def test_day_number_advances_to_two():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert state.day_number == 2
    assert state.deaths[0].day_number == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_night.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.night'`

- [ ] **Step 3: Write the implementation**

Create `src/the_village/night.py`:

```python
import random

from the_village.state import Death, GameState


def resolve_night_one(
    state: GameState, rng: random.Random | None = None
) -> GameState:
    rng = rng or random.Random()

    eligible = [
        v for v in state.villagers if v.player_type == "villager" and v.is_alive
    ]
    victim = rng.choice(eligible)
    victim.is_alive = False

    new_day = state.day_number + 1
    state.deaths.append(Death(name=victim.name, day_number=new_day))
    state.day_number = new_day

    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_night.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/the_village/night.py tests/test_night.py
git commit -m "feat: add night-one random kill resolution logic"
```

---

### Task 4: VillageFlow wiring

**Files:**
- Modify: `src/the_village/main.py` (replace the existing stub `VillageFlow`)
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: `GameState` (Task 1); `build_initial_roster` (Task 2); `resolve_night_one` (Task 3).
- Produces: `VillageFlow(Flow[GameState])` with `@start() setup_game` and `@listen(setup_game) run_night_one` methods; `kickoff()` (module-level function, CLI smoke test); `plot()` (unchanged from existing stub).

- [ ] **Step 1: Write the failing test**

Create `tests/test_flow.py`:

```python
from the_village.main import VillageFlow


def test_village_flow_produces_valid_night_one_result():
    flow = VillageFlow()
    flow.kickoff(inputs={"player_name": "Dana"})
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
Expected: FAIL — `state.deaths` is empty because `VillageFlow` doesn't yet run setup/night logic (current stub's `@start() start` is a no-op).

- [ ] **Step 3: Write the implementation**

Replace the contents of `src/the_village/main.py`:

```python
#!/usr/bin/env python
from crewai.flow import Flow, listen, start

from the_village.night import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState


class VillageFlow(Flow[GameState]):
    @start()
    def setup_game(self):
        roster_state = build_initial_roster(self.state.player_name)
        self.state.day_number = roster_state.day_number
        self.state.villagers = roster_state.villagers

    @listen(setup_game)
    def run_night_one(self):
        resolve_night_one(self.state)


def kickoff():
    village_flow = VillageFlow()
    village_flow.kickoff(inputs={"player_name": "TestPlayer"})
    print(village_flow.state.model_dump_json(indent=2))


def plot():
    village_flow = VillageFlow()
    village_flow.plot()


if __name__ == "__main__":
    kickoff()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_flow.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS (all tests from Tasks 1-4)

- [ ] **Step 6: Commit**

```bash
git add src/the_village/main.py tests/test_flow.py
git commit -m "feat: wire VillageFlow to run roster setup and night-one kill"
```

---

### Task 5: Gradio UI

**Files:**
- Create: `src/the_village/ui.py`
- Modify: `pyproject.toml` (add `gradio` dependency, add `app` script entry)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `VillageFlow` (Task 4); `GameState`, `Death`, `WEEKDAYS` (Task 1).
- Produces: `format_event_log(state: GameState) -> str`; `format_deaths_panel(state: GameState) -> str`; `start_game(player_name: str) -> tuple`; `build_app() -> gradio.Blocks`; `main()` (launches the app).

- [ ] **Step 1: Add the gradio dependency**

Run: `uv add gradio`

- [ ] **Step 2: Add the `app` script entry**

In `pyproject.toml`, add to `[project.scripts]`:

```toml
app = "the_village.ui:main"
```

- [ ] **Step 3: Write the failing tests for the pure formatting functions**

Create `tests/test_ui.py`:

```python
from the_village.state import Death, GameState, Villager
from the_village.ui import format_deaths_panel, format_event_log


def make_state_with_one_death() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager", is_alive=False),
    ]
    return GameState(
        player_name="Dana",
        day_number=2,
        villagers=villagers,
        deaths=[Death(name="A", day_number=2)],
    )


def test_format_event_log_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_event_log(state) == "Nothing has happened yet."


def test_format_event_log_with_a_death():
    state = make_state_with_one_death()
    assert format_event_log(state) == "Monday morning: A was found dead."


def test_format_deaths_panel_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_deaths_panel(state) == "No one has been killed yet."


def test_format_deaths_panel_with_a_death():
    state = make_state_with_one_death()
    assert format_deaths_panel(state) == "Monday: A"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'the_village.ui'`

- [ ] **Step 5: Write the implementation**

Create `src/the_village/ui.py`:

```python
import gradio as gr

from the_village.main import VillageFlow
from the_village.state import WEEKDAYS, GameState


def format_event_log(state: GameState) -> str:
    if not state.deaths:
        return "Nothing has happened yet."
    lines = [
        f"{WEEKDAYS[(death.day_number - 1) % 7]} morning: {death.name} was found dead."
        for death in state.deaths
    ]
    return "\n\n".join(lines)


def format_deaths_panel(state: GameState) -> str:
    if not state.deaths:
        return "No one has been killed yet."
    lines = [
        f"{WEEKDAYS[(death.day_number - 1) % 7]}: {death.name}"
        for death in state.deaths
    ]
    return "\n".join(lines)


def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    try:
        flow = VillageFlow()
        flow.kickoff(inputs={"player_name": player_name.strip()})
    except gr.Error:
        raise
    except Exception:
        raise gr.Error("Something went wrong, please try again.")

    state = flow.state
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="The Village") as demo:
        with gr.Column(visible=True) as start_screen:
            name_input = gr.Textbox(label="Your first name")
            start_button = gr.Button("Start Game")

        with gr.Row(visible=False) as result_screen:
            with gr.Column():
                gr.Markdown("### Events")
                event_log = gr.Markdown()
            with gr.Column():
                gr.Markdown("### Killed by Werewolves")
                deaths_panel = gr.Markdown()

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[start_screen, result_screen, event_log, deaths_panel],
        )

    return demo


def main():
    build_app().launch()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS (all tests from Tasks 1-5)

- [ ] **Step 8: Manual browser verification**

Run: `uv run app`

In a browser, go to the printed local URL (typically `http://127.0.0.1:7860`):
1. Leave the name field blank and click "Start Game" — confirm an inline error is shown and no result screen appears.
2. Enter a name (e.g. "Dana") and click "Start Game" — confirm the start screen is replaced by a two-column result screen.
3. Confirm the left column ("Events") shows a line like "Monday morning: `<name>` was found dead."
4. Confirm the right column ("Killed by Werewolves") shows a line like "Monday: `<name>`".
5. Confirm the name shown as killed is never "Dana" (the player).

Stop the server (Ctrl+C) once verified.

- [ ] **Step 9: Commit**

```bash
git add src/the_village/ui.py tests/test_ui.py pyproject.toml uv.lock
git commit -m "feat: add Gradio UI for starting a game and viewing night-one results"
```
