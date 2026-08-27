# Win Condition — Design

## Context

`VillageFlow`'s day/night cycle has no win condition today — the module
docstring on `_AUTO_PLAY_MAX_DAYS` says so directly: "VillageFlow's
day/night cycle has no win condition yet and loops forever, so the CLI
smoke test below has to stop itself after a few days rather than waiting
for the flow to finish on its own." Players die at exactly two points —
`kill_first_victim`/`WereWolfPack.kill_next_victim` at night, and
`Voting._tally`'s lynch — but nothing ever checks whether either death
ended the game, and the flow has no terminal state to reach if it did.

This design adds that check at both death points, a terminal `game_over`
step in `VillageFlow`, and a results panel in `ui.py` that reveals the
winner and who the werewolves were.

Scope:

1. A `Winner` type and win-condition check on `GameState`.
2. A `GameOverResult` bridge message and a terminal `finish_game` step in
   `VillageFlow`, reached by routing around the normal night/discussion/
   vote cycle once a winner is decided.
3. A results panel in `ui.py`, shown alongside (not replacing) the
   existing history/live-day-card UI, with a "Play Again" button that
   starts an entirely new `VillageFlow`.

Out of scope: anything about *how* players are killed or vote (unchanged
from the existing night/discussion/voting designs) — this only adds the
win check and the game's ending.

## Win condition

Werewolves never target each other at night
(`WereWolfPack._eligible_targets` excludes werewolves), so a werewolf can
only die by lynching — "every werewolf has been lynched" and "no living
werewolves remain" are equivalent, letting the check be a simple count
comparison rather than tracking *how* each werewolf died.

In `state.py`:

```python
Winner = Literal["villagers", "werewolves"]

class GameState(BaseModel):
    ...
    winner: Winner | None = None

    def living_werewolves_count(self) -> int:
        return sum(1 for p in self.players if p.is_werewolf and p.is_alive)

    def living_non_werewolves_count(self) -> int:
        return sum(1 for p in self.players if p.is_not_werewolf and p.is_alive)

    def determine_winner(self) -> Winner | None:
        if self.living_werewolves_count() == 0:
            return "villagers"
        if self.living_werewolves_count() >= self.living_non_werewolves_count():
            return "werewolves"
        return None

    def werewolf_names(self) -> list[str]:
        return [p.name for p in self.players if p.is_werewolf]
```

`determine_winner()` is a plain method (not a cached property) called at
the two points a death occurs; its result is stored on `state.winner` so
`VillageFlow`'s routers can branch on it without recomputing, and so
`ui.py` can read it directly off `GameState` if needed.
`living_non_werewolves_count()` counts the user alongside AI villagers
(`Player.is_not_werewolf`, already `player_type != "werewolf"`), matching
"villagers (including the user)" from the requirement.

## `VillageFlow` — terminal routing

```python
@router(setup_game)
async def run_night_one(self):
    kill_first_victim(self.state)
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
    await Discussion(...).run()
    await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
    await self.bridge.wait_for_input()

@listen(run_discussion)
async def run_voting(self):
    outcome = await Voting(...).run()
    self.state.winner = self.state.determine_winner()
    if not self.state.winner:
        self.state.advance_day()
    await self.bridge.outbox.put(outcome)
    await self.bridge.outbox.put(FlowStatus.VOTING_COMPLETE)

@router(run_voting)
async def run_next_night(self):
    if self.state.winner:
        return "game_over"
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

This checks the winner at exactly the two places a death happens:

- **Night kill** — `check_winner_after_kill` runs right after
  `announce_death`'s pause resolves (i.e. after the player clicks
  "Begin"), so the death is always shown before the game can end on it,
  per the confirmed UX. If no winner, it emits `"discussion"` (renamed
  from an earlier `"continue_after_kill"` working name) and
  `run_discussion` proceeds exactly as before.
- **Lynch** — `run_voting` computes the winner immediately after tallying
  and, only when there isn't one, advances the day (so a game-ending
  lynch doesn't create a dangling extra `Day`). `run_next_night`'s router
  then branches on the stored `state.winner` before doing the next
  night's kill — matching the confirmed UX where a lynch-triggered ending
  resolves right after the vote result, with no extra click (mirroring
  how voting already auto-advances to the next day today).

`finish_game` is the flow's new terminal step; nothing listens after it,
so `VillageFlow.kickoff_async()` now completes naturally once a winner is
decided. This makes `village_flow.py`'s `_AUTO_PLAY_MAX_DAYS` /
`_auto_play_consumer` day-counting workaround unnecessary — the CLI smoke
test can drain the outbox until the flow task itself finishes instead of
counting `VOTING_COMPLETE` events, so that scaffolding is removed as part
of this change.

## Bridge

In `bridge.py`:

```python
@dataclass
class GameOverResult:
    winner: Winner
    werewolf_names: list[str]
```

`SessionBridge`'s outbox type comment gains `| GameOverResult`. This is
the last item ever put on a given session's outbox — nothing follows it,
matching `DISCUSSION_COMPLETE`/`VOTING_COMPLETE`'s existing role as a
stream-ending marker but for the whole game rather than one round.

## UI (`ui.py`)

**New component**, built alongside the existing `live-day-card` inside
`result_screen` (not replacing it — history and the frozen final round
stay visible per the confirmed placement):

```python
with gr.Column(visible=False, elem_classes=[GAME_OVER_PANEL_CLASS]) as game_over_panel:
    game_over_status = gr.Markdown()
    play_again_button = gr.Button("Play Again")
```

`format_game_over(result: GameOverResult, state: GameState) -> str`
renders a headline ("The Villagers Win!" / "The Werewolves Win!") and a
"The werewolves were:" line with each name as a colored chip, reusing
`_speaker_color_index`/`VILLAGER_CHIP_CLASS` the same way
`format_deaths_panel`/`format_lynched_panel` already do, so the reveal
matches the rest of the chronicle's visual language.

**Reaching the panel** — both existing generators that can observe a
game-ending event gain one new branch each, matched against
`GameOverResult` the same way they already match `FlowFailed`,
`DiscussionMessage`, `VoteOutcome`, etc.:

- `_stream_bridge` (drives the kill-triggered path, entered via
  `begin_discussion` after "Begin" is clicked): on `GameOverResult`, fold
  the just-finished (death-only) day into `history_log` via
  `format_completed_round_history`, hide the live day card's interactive
  elements (Begin button, discussion transcript/input), show
  `game_over_panel` with `format_game_over(...)`, and return — ending the
  stream instead of continuing into `DISCUSSION_COMPLETE`.
- `start_voting` and `cast_player_vote` (drive the lynch-triggered path —
  both need the same branch since either can be the one draining the
  final `VOTING_COMPLETE`, exactly as they already duplicate the
  next-day-setup branch today): on `GameOverResult`, same treatment —
  fold the final day into `history_log` (this day already has its
  discussion/vote content, same folding `_next_day_setup` already does
  for a normal day transition), hide the vote/discussion widgets, show
  `game_over_panel`, and return instead of calling `_next_day_setup`.

Each of these generators' `outputs=[...]` lists (`discussion_outputs`,
`vote_outputs`, and the `discussion_status.change` outputs) gains
`game_over_panel` (and, where the panel's own content changes,
`game_over_status`) as one more slot, `gr.update()` no-op in every branch
that isn't the `GameOverResult` one — the same mechanical pattern the
file already uses for every other cross-cutting output.

**Play Again** — `play_again_button.click` calls the existing
`start_game` function, unchanged, passing `state.user_player_name` as
the name input. `start_game` already constructs a fresh `SessionBridge` +
`VillageFlow` and resets `start_screen`/`result_screen` visibility, which
satisfies "an entirely new `VillageFlow`" with no new function needed;
its `outputs` list gains `game_over_panel` reset to `visible=False` so a
second game doesn't start with the previous one's results still showing.

## Error handling

Unchanged pattern: any exception raised while determining or announcing
the winner is caught by `run_flow`'s existing broad handler and surfaces
as `FlowFailed` on the outbox, rendered by `ui.py`'s existing `gr.Error`
handling — no new error path needed.

## Testing

- `tests/test_state.py` — `determine_winner()` cases: all werewolves
  dead → `"villagers"`; living werewolves ≥ living non-werewolves →
  `"werewolves"` (including the mislynch case where lynching a villager
  tips the balance); neither condition met → `None`.
- `tests/test_flow.py` — drive `VillageFlow` through a night kill that
  immediately decides the game (stub the roster/RNG down to parity) and
  assert `GameOverResult` lands on the bridge after the death
  announcement's pause resolves, with no `DISCUSSION_COMPLETE` following;
  separately, drive it through a lynch that kills the last werewolf and
  assert `GameOverResult` follows `VOTING_COMPLETE` with no next
  `player_found_dead`. Existing multi-day tests continue to exercise the
  no-winner-yet path unchanged.
- Manual pass in the browser: play to a lynch-triggered villager win and
  a kill-triggered werewolf win (or force via a small player count),
  confirming the results panel appears alongside history with the
  correct werewolf names, and that "Play Again" starts a fresh game.
