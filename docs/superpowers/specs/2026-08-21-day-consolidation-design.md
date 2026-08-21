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
class Death(BaseModel):
    name: str

class DiscussionMessage(BaseModel):
    speaker: str
    message: str
    addressed_to: str | None = None

class VoteRecord(BaseModel):
    voter: str
    target: str | None = None

class Lynching(BaseModel):
    name: str

class Day(BaseModel):
    day_number: int
    death: Death | None = None
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    lynching: Lynching | None = None

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

    def advance_day(self, death: Death | None = None) -> Day:
        new_day = Day(day_number=self.day_number + 1, death=death)
        self.days.append(new_day)
        return new_day

    def all_discussion(self) -> list[DiscussionMessage]:
        return [message for day in self.days for message in day.discussion]
```

`Death`, `DiscussionMessage`, `VoteRecord`, and `Lynching` drop their
`day_number` field — each is only ever reached through its owning `Day`, so
the field would just be a second copy of `Day.day_number`.

`GameState.day_number` and `GameState.current_day` mean most existing
read-only call sites (UI weekday labels, flow logging) don't change at all;
they just now read a property instead of a field. `advance_day()` replaces
the "compute new day number, stamp a `Death`, assign the counter" sequence
that today lives inline in `night.py`. `all_discussion()` exists because
two call sites (the discussion transcript formatter and
`DiscussionRunner.run()`'s return value) need the full cross-day message
list, not "today's" — a flatten is simpler than exposing day structure to
callers that don't care about it.

`VoteOutcome` (`voting.py`) is a separate, transient return model — not
part of `GameState` — and keeps its own `day_number` field unchanged; this
design only touches `GameState`'s owned models.

## Call-site changes

- **`roster.py`** `build_initial_roster()`: drop the explicit
  `day_number=1` kwarg passed to `GameState(...)` — it's now the default.
- **`night.py`** `resolve_night_one()`: replace the manual
  `new_day = state.day_number + 1` / `state.deaths.append(Death(name=...,
  day_number=new_day))` / `state.day_number = new_day` sequence with a
  single `state.advance_day(death=Death(name=victim.name))` call.
- **`voting.py`**:
  - `_format_lynchings()` iterates `state.days`, formatting each day that
    has a `lynching` using `day.day_number` and `day.lynching.name`
    (instead of filtering a flat `state.lynchings` list by stamped
    `day_number`).
  - `cast_votes()` builds `VoteRecord(voter=name, target=target)` (no
    `day_number`) into `state.current_day.votes`, and
    `Lynching(name=lynched)` assigned to `state.current_day.lynching`.
  - `VoteOutcome` construction is unchanged (still reads `state.day_number`
    for its own field).
- **`discussion/discussion.py`**:
  - `_format_deaths()` iterates `state.days`, formatting each day that has
    a `death` using `day.day_number` and `day.death.name`.
  - `_format_history()` iterates `state.days` then each `day.discussion`
    entry, same join-by-speaker output as today.
  - `_record_message()` builds `DiscussionMessage(speaker=..., message=...,
    addressed_to=...)` (no `day_number`) and appends to
    `state.current_day.discussion`.
  - `DiscussionRunner.run()` returns `self.state.all_discussion()` instead
    of `self.state.discussion`.
  - The two logging statements that currently read `message.day_number` /
    `reply.day_number` log `state.day_number` instead (the day being
    discussed, not a field on the message).
- **`village_flow.py`**:
  - `setup_game()`'s `self.state.day_number = roster_state.day_number`
    becomes `self.state.days = roster_state.days`.
  - `announce_death()`'s `self.state.deaths[-1]` becomes
    `self.state.current_day.death`.
  - The debug log of `len(self.state.discussion)` becomes
    `len(self.state.all_discussion())`.
- **`ui.py`**:
  - `format_event_log()` and `format_deaths_panel()` iterate `state.days`,
    reading `day.day_number` / `day.death` for each day that has one
    (instead of iterating flat `state.deaths`).
  - `format_lynched_panel()` iterates `state.days`, reading
    `day.day_number` / `day.lynching`.
  - `format_discussion_transcript()` uses `state.all_discussion()` in place
    of `state.discussion`; the existing `[:-1]` "hide last message while
    typing placeholder shows" slice is unchanged.
  - `begin_discussion()` and `begin_voting()` are unaffected — they read
    `state.day_number`, which continues to work via the property.
- **Tests** (`test_state.py`, `test_night.py`, `test_roster.py`,
  `test_voting.py`, `test_ui.py`, `discussion/test_discussion.py`,
  `test_flow.py`): every direct construction of `Death`,
  `DiscussionMessage`, `VoteRecord`, or `Lynching` with an explicit
  `day_number=...` kwarg, and every assertion against
  `state.deaths`/`state.discussion`/`state.votes`/`state.lynchings`, is
  rewritten against `state.days` / `Day` / the new `GameState` helpers.

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
