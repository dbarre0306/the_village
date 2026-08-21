# Day Consolidation — Design

## Context

`GameState` currently tracks the game's day as a bare `day_number: int`
field, alongside four flat lists (`deaths`, `discussion`, `votes`,
`lynchings`) whose entries each carry their own `day_number` stamp. Every
place that constructs a `Death`, `DiscussionMessage`, `VoteRecord`, or
`Lynching` has to know and repeat the current day number, and every place
that renders "what happened on day N" has to filter or associate list
entries back to a day by matching that duplicated field. There is no single
owner of "day N's events" — it's reconstructed ad hoc wherever needed.

This design introduces a `Day` model that owns everything that happened on
a given day. `GameState` becomes a list of `Day`s (plus players and the
player's name) instead of a day counter plus four parallel lists.

Confirmed via codebase search: `GameState` is never persisted to disk or
loaded from a file (the only serialization is a debug `model_dump_json()`
print in `main.py`'s CLI smoke-test path). This is a pure in-code
refactor — no migration format to preserve.

## Data Model

```python
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
        ...  # unchanged

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

`Death` and `Lynching` are dropped as classes entirely — each was already
just a `name: str` wrapper once `day_number` came out (see below), and
nothing in the codebase pattern-matches on them as distinct types (checked
`bridge.py`/`ui.py`'s outbox consumers: only `DiscussionMessage` and
`FlowFailed` are ever `isinstance`-checked there). `Day` just stores the
player's name directly as `player_killed` / `player_lynched`.

`DiscussionMessage` and `VoteRecord` drop their `day_number` field — each is
only ever reached through its owning `Day`, so the field would just be a
second copy of `Day.day_number`.

`GameState.day_number` and `GameState.current_day` mean most existing
read-only call sites (UI weekday labels, flow logging) don't change at all;
they just now read a property instead of a field. `advance_day()` replaces
the "compute new day number, stamp a `Death`, assign the counter" sequence
that today lives inline in `night.py`.

No cross-day flatten helper (e.g. an `all_discussion()`) is added.
`DiscussionRunner.run()` now returns only the current day's discussion —
nothing downstream ever consumed a cross-day return value from it (see call
site changes below). The two remaining places that render history across
every day — `_format_history()` and `ui.py`'s transcript panel — walk
`state.days` directly and pull `day.discussion` themselves; a
discussion-only flatten wouldn't have served the transcript panel anyway,
since that panel's day-by-day view also needs each day's `player_killed`,
`votes`, and `player_lynched`, not just its messages.

`VoteOutcome` (`voting.py`) is a separate, transient return model — not
part of `GameState` — and keeps its own `day_number` field unchanged; this
design only touches `GameState`'s owned models.

## Call-site changes

- **`roster.py`** `build_initial_roster()`: drop the explicit
  `day_number=1` kwarg passed to `GameState(...)` — it's now the default.
- **`night.py`** `resolve_night_one()`: replace the manual
  `new_day = state.day_number + 1` / `state.deaths.append(Death(name=...,
  day_number=new_day))` / `state.day_number = new_day` sequence with a
  single `state.advance_day(player_killed=victim.name)` call.
- **`voting.py`**:
  - `_format_lynchings()` iterates `state.days`, formatting each day that
    has a `player_lynched` using `day.day_number` and `day.player_lynched`
    (instead of filtering a flat `state.lynchings` list by stamped
    `day_number`).
  - `cast_votes()` builds `VoteRecord(voter=name, target=target)` (no
    `day_number`) into `state.current_day.votes`, and assigns the winning
    name directly: `state.current_day.player_lynched = lynched`.
  - `VoteOutcome` construction is unchanged (still reads `state.day_number`
    for its own field).
- **`discussion/discussion.py`**:
  - `_format_deaths()` iterates `state.days`, formatting each day that has
    a `player_killed` using `day.day_number` and `day.player_killed`.
  - `_format_history()` iterates `state.days` then each `day.discussion`
    entry, same join-by-speaker output as today.
  - `_record_message()` builds `DiscussionMessage(speaker=..., message=...,
    addressed_to=...)` (no `day_number`) and appends to
    `state.current_day.discussion`.
  - `DiscussionRunner.run()` returns `self.state.current_day.discussion`
    instead of `self.state.discussion` — only today's transcript, not every
    day's. Nothing downstream currently captures this return value (
    `village_flow.py`'s `run_discussion()` calls `.run()` without assigning
    it), so this is a genuine scope correction rather than a behavior-
    preserving rename.
  - The two logging statements that currently read `message.day_number` /
    `reply.day_number` log `state.day_number` instead (the day being
    discussed, not a field on the message).
- **`village_flow.py`**:
  - `setup_game()`'s `self.state.day_number = roster_state.day_number`
    becomes `self.state.days = roster_state.days`.
  - `announce_death()`'s `self.state.deaths[-1]` becomes
    `self.state.current_day.player_killed` — a bare `str` (the victim's
    name) instead of a `Death` object. It's put on `bridge.outbox`
    unchanged otherwise; nothing downstream reads it (`start_game()`'s
    consumer only checks for `FlowFailed`, then re-renders panels from
    `state`), so `bridge.py`'s outbox docstring (`DiscussionMessage | Death
    | FlowStatus`) is updated to say `DiscussionMessage | str | FlowStatus`.
  - The debug log of `len(self.state.discussion)` becomes
    `len(self.state.current_day.discussion)` — it logs how many messages
    today's discussion run produced, matching `run_discussion()`'s own
    scope now that `DiscussionRunner.run()` returns only today's
    transcript.
- **`ui.py`**:
  - `format_event_log()` and `format_deaths_panel()` iterate `state.days`,
    reading `day.day_number` / `day.player_killed` for each day that has
    one (instead of iterating flat `state.deaths`).
  - `format_lynched_panel()` iterates `state.days`, reading
    `day.day_number` / `day.player_lynched`.
  - `format_discussion_transcript()` iterates `state.days`, flattening each
    day's `discussion` in place of reading flat `state.discussion`; the
    existing `[:-1]` "hide last message while typing placeholder shows"
    slice is unchanged (still applied to the flattened result).
  - `begin_discussion()` and `begin_voting()` are unaffected — they read
    `state.day_number`, which continues to work via the property.
- **Tests** (`test_state.py`, `test_night.py`, `test_roster.py`,
  `test_voting.py`, `test_ui.py`, `discussion/test_discussion.py`,
  `test_flow.py`): every direct construction of the old `Death`,
  `DiscussionMessage`, `VoteRecord`, or `Lynching` models with an explicit
  `day_number=...` kwarg, and every assertion against
  `state.deaths`/`state.discussion`/`state.votes`/`state.lynchings`, is
  rewritten against `state.days` / `Day` / the new `GameState` helpers —
  including replacing `Death(name=...)`/`Lynching(name=...)` construction
  with plain strings assigned to `Day.player_killed`/`Day.player_lynched`.

## Out of scope

- No change to `VoteOutcome`, `TurnOutput`, `Player`, or any other model
  not listed above.
- No change to the multi-round discussion mechanics, night resolution
  logic, or voting/lynching logic themselves — only how their results are
  stored and retrieved from `GameState`.
- No persistence/serialization format — none exists today, so none is
  introduced.

## Testing

- Existing test suite continues to serve as the correctness check: every
  test that constructs the old flat models or asserts against the old flat
  lists is updated to the `Day`-based shape, and should assert the same
  observable behavior (day advancement, death/lynching/discussion/vote
  recording, formatted output strings) as before.
- No new behavior is being added, so no new test *cases* are needed beyond
  translating existing ones to the new shape — `crewai test` /
  `pytest` passing is the acceptance bar.
