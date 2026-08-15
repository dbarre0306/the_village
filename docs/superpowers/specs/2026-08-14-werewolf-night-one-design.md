# Werewolf Night-One Meeting — Design

## Context

`the_village` is a single-player social-deduction game (Werewolf/Mafia rules)
that can be played concurrently by many users. The full game (day-phase
voting, special roles like the Seer, multiple nights, win conditions) is out
of scope for this design. This design covers only:

1. Game setup when a player starts a new game (roster generation, werewolf
   assignment).
2. The werewolves' first-night decision of who to kill.
3. The Gradio UI that triggers this and displays the result.

Everything here is scoped to **night one**. Subsequent nights, day-phase
voting, and additional roles are explicitly future work, but the data model
is shaped so those can be added later without a rewrite.

## Why no CrewAI Crew for the kill decision

The original idea was to model each werewolf as a CrewAI `Agent` that
discusses and votes on a target. On night one, however, there is no
day-phase history, no accusations, and no public game state yet — the
werewolves have no information to reason about. An LLM discussion in that
situation is theater: it adds latency, LLM cost, and failure modes (a model
picking an invalid target, non-determinism in tests) for a decision that is,
information-theoretically, a coin flip.

The design therefore makes the night-one kill a plain random choice in code.
No `Agent`, `Task`, `Crew`, or guardrail is needed for this build. A
`Crew`-based discussion becomes worth reconsidering once a day phase exists
and werewolves have accusations/suspicion to reason about — that is future
work, not part of this design.

The project's `Flow` scaffold (`src/the_village/main.py`,
`pyproject.toml`'s `[tool.crewai] type = "flow"`) is still the right
container: it gives us a structured, typed `GameState` and a natural place
(`@listen` steps) to add a day phase later, even though today's "meeting"
step is deterministic Python rather than an LLM call.

## Architecture

```
Gradio UI (single page, two states: start screen / result screen)
  ├─ Start screen: textbox for player's first name + "Start Game" button
  └─ Result screen: two-column layout
        ├─ Left  — chronological event log
        └─ Right — persistent list of werewolf kills
        │
        ▼
VillageFlow (CrewAI Flow)
  ├─ @start()  setup_game(player_name) -> GameState
  │     - sample 6 AI villager names from a fixed name pool
  │     - roster = player ("user") + 6 AI villagers ("villager")
  │     - randomly reassign 2 of the AI villagers to player_type="werewolf"
  │     - flag one of the two werewolves is_pack_leader=True
  │
  └─ @listen(setup_game)  run_night_one(state) -> GameState
        - eligible targets = villagers with player_type == "villager" and is_alive
        - killed = random.choice(eligible targets)
        - mark that villager is_alive = False
        - append Death(name=killed.name, day_number=state.day_number + 1) to state.deaths
        - increment state.day_number
```

Game state is intentionally transient for this night-one-only build: each
"Start Game" click builds a fresh `VillageFlow()`, kicks it off, and renders
its resulting `GameState` directly into the response — nothing is persisted
server-side beyond that single request/response, and there is no
`gr.State`. There is no shared/global mutable state, so concurrent users'
games cannot collide. A future multi-night task would need to introduce
session-scoped state (e.g. `gr.State`) at that point, to carry a game
forward across turns within a session.

## Data Model

```python
from typing import Literal
from pydantic import BaseModel

PlayerType = Literal["user", "villager", "werewolf"]

WEEKDAYS = [
    "Sunday", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday",
]

class Villager(BaseModel):
    name: str
    player_type: PlayerType
    is_pack_leader: bool = False
    is_alive: bool = True

class Death(BaseModel):
    name: str
    day_number: int  # the day the body was found (morning after the kill)

class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1  # 1 = Sunday, the game's first day
    villagers: list[Villager] = []
    deaths: list[Death] = []
```

Notes:

- `day_number` starts at `1`, mapped to `"Sunday"` via `WEEKDAYS[(day_number
  - 1) % 7]`. The game starts on a Sunday; the first night (Sunday night)
  resolves as a death reported "Monday morning" (`day_number=2`).
- `player_type` uses a 3-value `Literal` instead of separate booleans so a
  future `"seer"` value (and any other role) can be added without
  restructuring the model.
- Roster size is fixed for this build: 7 total (1 `"user"` + 6 AI
  villagers), 2 of the 6 AI villagers are werewolves, leaving 4 eligible
  night-one kill targets. Not configurable via UI yet.
- `Death` is a list, not a single field, so the UI can always render the
  full history of werewolf kills — required even though night one produces
  only one entry, because the shape needs to hold up once more nights are
  added.

## Setup Logic (`setup_game`)

1. Take `player_name` as input (already validated non-blank by the UI layer
   — see Error Handling).
2. Sample 6 distinct names from a fixed hardcoded name pool (~10-15 names)
   without replacement, first excluding any pool name that matches the
   player's name (case-insensitive) — so no AI villager shares the
   player's name.
3. Build the roster: one `Villager(name=player_name, player_type="user")`
   plus 6 `Villager(name=..., player_type="villager")`.
4. Randomly choose 2 of the 6 AI villagers and set `player_type="werewolf"`
   on them.
5. Randomly choose 1 of those 2 werewolves and set `is_pack_leader=True`.
   (Pack leader has no effect in this build — night one's kill is a random
   choice, not a vote — but is assigned now since it's part of a werewolf's
   identity and will matter once a Crew-based discussion is introduced for
   later nights.)
6. Return the populated `GameState` with `day_number=1`.

All of this is deterministic Python (seedable RNG for tests) — no LLM
calls.

## Night-One Kill Logic (`run_night_one`)

1. Compute eligible targets: villagers where `player_type == "villager"
   and is_alive`. On night one this is always the 4 non-werewolf AI
   villagers (the player is never eligible, and werewolves don't target
   each other).
2. `killed = random.choice(eligible_targets)`.
3. Set `killed.is_alive = False`.
4. Append `Death(name=killed.name, day_number=state.day_number + 1)` to
   `state.deaths`.
5. Increment `state.day_number`.
6. Return the updated `GameState`.

The player is excluded from `eligible_targets` by construction (their
`player_type` is `"user"`, not `"villager"`), so there's no separate "don't
kill the player on night one" special case to maintain.

## Gradio UI

**Start screen:**
- Textbox for the player's first name.
- "Start Game" button.

**On submit:**
- Reject blank/whitespace-only names inline; don't kick off the flow.
- Disable the button, show a loading state while `VillageFlow().kickoff(
  inputs={"player_name": name})` runs synchronously. The click handler sets
  `concurrency_limit=None`, since Gradio's default limit of 1 would
  otherwise serialize every session's "Start Game" click globally; with no
  shared mutable state to protect (see above), concurrent sessions can run
  their flows in parallel without blocking each other.
- On success, switch to the result screen.

**Result screen (two columns):**
- **Left — event log:** chronological, player-visible sentences derived
  from `state.deaths`, e.g. `"Monday morning: <name> was found dead."` This
  is the future home for day-phase events (accusations, votes) once those
  are built — nothing else to design for that now.
- **Right — deaths panel:** persistent list of every werewolf kill so far,
  formatted as `"<weekday>: <name>"` per entry, driven by the same
  `state.deaths` list.

The werewolf meeting itself is never shown to the player — consistent with
what a villager character would actually know.

## Error Handling

- Blank name on submit → inline validation message; flow never kicks off.
- Any exception from `Flow.kickoff()` (e.g. unexpected internal error) is
  caught at the Gradio callback boundary and shown as a generic "something
  went wrong, please try again" message rather than a raw stack trace or a
  silent hang.
- No retry/fallback logic beyond input validation — since there's no LLM
  call in this build, there's no guardrail/retry surface to design for
  here.

## Testing

- Unit test `setup_game` with a seeded RNG: roster is 7 villagers, exactly
  1 `"user"`, exactly 2 `"werewolf"`, exactly 1 `is_pack_leader=True` among
  the werewolves.
- Unit test `run_night_one`: killed villager is always `player_type ==
  "villager"`, never the player or a werewolf; `deaths` gets exactly one
  new entry with `day_number == 2`; `day_number` increments.
- Flow-level test: run `VillageFlow().kickoff(...)` end-to-end (seeded RNG)
  and assert final `GameState` invariants (one death, correct day number,
  roster counts unchanged).
- Manual pass in the browser: launch the Gradio app, enter a name, click
  Start, confirm the two-column result screen renders correctly — this is
  the acceptance check for the UI wiring, since it isn't covered by the
  unit/flow tests above.

## Explicitly Out of Scope

- Day-phase voting/accusations.
- The Seer or any other special role.
- Multiple nights / repeat play of the meeting.
- Win condition detection / game-over state.
- Configurable roster size.
- Persisting game state across server restarts.
