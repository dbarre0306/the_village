# Voting / VillageFlow Integration — Design

## Context

[Lynch voting](2026-08-18-lynch-voting-design.md) added `voting.py` as a
plain, synchronous module driven directly by the Gradio UI: `ui.py` calls
`cast_votes(state, agents, player_vote)` the instant the player clicks a
ballot button, blocking on every AI villager's `kickoff()` in that same
callback. That design deliberately kept voting outside `VillageFlow` —
tallying was "deterministic bookkeeping," not a `Flow` step.

Since then, [discussion was rebuilt](2026-08-19-discussion-crew-flow-redesign-design.md)
as an async `VillageFlow` step (`run_discussion`) that pauses on
`SessionBridge.wait_for_input()` for the human's turn, and its internals
were [split into a package](2026-08-18-lynch-voting-design.md) —
`discussion/` with a private `_Speaker` base class and `_AiSpeaker`/
`_HumanSpeaker` subclasses, only `Discussion` exported publicly.

This design brings voting into that same shape: a `voting/` package
structured like `discussion/`, wired into `VillageFlow` as the step after
discussion, with the human's vote collected via the bridge pause instead
of a direct UI call into vote-casting logic.

Scope:

1. Restructure `voting.py` into a `voting/` package mirroring
   `discussion/`'s internals-private convention.
2. Add a `run_voting` step to `VillageFlow`, gated behind a new pause
   (mirroring the night-one → discussion pause) so "Begin Voting" remains
   a deliberate beat between phases.
3. Collect the human's vote through `SessionBridge`, the same pause
   mechanism `_HumanSpeaker` uses for discussion turns.
4. Switch AI vote casting from synchronous `agent.kickoff()` to the async
   `Crew(...).akickoff()` pattern `_AiSpeaker` already uses, for
   consistency and to stop blocking the event loop.
5. Update `ui.py`'s voting handlers to drive the bridge (`resolve_input` +
   drain the outbox) instead of calling vote-casting logic directly.

Out of scope: the day 2+ game loop (looping discussion → vote → night,
win-condition checks) — same explicit exclusion the original lynch-voting
design made; only today's single day is wired up.

## Why these boundaries, not others

Tallying and the majority/tie rule stay deterministic bookkeeping inside
`Voting`, not a `Crew`/`Flow` step of their own — same reasoning the
original design gave and still true. What's changing is only *how* each
individual's choice gets collected: the human's now suspends the Flow via
the bridge instead of the UI calling into vote logic directly, and AI
votes run through the same async `Crew` machinery discussion already
established, instead of a separate synchronous path.

## Package Structure (`voting/`)

Mirrors `discussion/`'s file layout and privacy convention (only
`Voting` and `VoteOutcome` exported via `__init__.py`'s `__all__`):

- **`voter.py`** — private `_Voter` base class. Holds `state`/`bridge`/
  `player_name`. Public `cast()` calls the abstract `_cast()` then
  appends a `VoteRecord` to `state.current_day.votes` — the same
  centralize-the-recording role `_Speaker._record_message` plays for
  discussion. Also owns `_resolve_target`, identical validation to
  `_Speaker._resolve_target` (must name a living villager other than the
  voter; anything else — self-vote, dead villager, unrecognized name, or
  unset — normalizes to abstain). This is duplicated rather than shared
  with `discussion/`'s private helper, keeping each package's internals
  private to itself, the same boundary the recent discussion-package
  split established.
- **`ai_voter.py`** — private `_AiVoter(_Voter)`. Builds a prompt from
  known facts (`state.format_deaths()`), prior lynchings (today's
  `_format_lynchings`, ported over unchanged), the full multi-day
  discussion transcript (`state.format_history()`), and the list of
  other living villagers. Runs it as a single-task `Crew(agents=[agent],
  tasks=[task]).akickoff()` against a private `_VoteChoice` output model
  (`target: str | None`) — one task, since voting needs no
  address-resolution step the way a discussion turn does.
- **`human_voter.py`** — private `_HumanVoter(_Voter)`. Puts
  `FlowStatus.WAITING_FOR_VOTE` on `bridge.outbox`, awaits
  `bridge.wait_for_input()`, resolves the returned `PlayerInput.text` as
  the chosen target (reusing the existing field — no new `PlayerInput`
  shape needed, same way discussion reuses `text` for pass/decline).
- **`voting.py`** — public `Voting` class:
  - `__init__(state, bridge, player_agents)` builds one `_Voter` per
    `state.names_of_living_players()` (human → `_HumanVoter`, AI →
    `_AiVoter`), same `_build_voters()`/`_build_voter()` shape as
    `Discussion._build_speakers`.
  - `async def run(self) -> VoteOutcome` casts every voter in
    `names_of_living_players()` order and tallies. No reordering needed:
    `roster.py` always places the human first in `state.players`, and
    `names_of_living_players()` preserves that order, so the human's
    turn — and the ballot — naturally comes up before any AI kickoff
    runs, preserving today's instant-ballot UX with no special-casing.
  - Tally logic is a direct port of today's `cast_votes` tail: count
    non-abstain votes among just-cast records, no votes → no lynch, a
    single strict-max target → lynch (`is_alive = False`,
    `state.current_day.player_lynched` set), a tie → no lynch.

`VoteRecord` stays in `state.py` (unchanged, shared with `Day.votes`).
`VoteOutcome` moves into `voting/voting.py` and stays public — `ui.py`
still needs the type for `format_vote_result` and for pattern-matching
items off the bridge.

## `VillageFlow` Integration

```python
@listen(announce_death)
async def run_discussion(self):
    await Discussion(
        state=self.state,
        bridge=self.bridge,
        player_agents=self._player_agents,
        analyst_agent=self._analyst_agent,
    ).run()
    await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
    await self.bridge.wait_for_input()          # NEW pause -- gates on "Begin Voting"

@listen(run_discussion)
async def run_voting(self):
    outcome = await Voting(
        state=self.state,
        bridge=self.bridge,
        player_agents=self._player_agents,
    ).run()
    await self.bridge.outbox.put(outcome)
    await self.bridge.outbox.put(FlowStatus.VOTING_COMPLETE)
```

`run_discussion`'s new trailing `wait_for_input()` mirrors
`announce_death`'s existing pause exactly — today, nothing on the Flow
side gates "Begin Voting" at all (the button is a bare UI-local reveal);
this makes it a real pause point the button resolves, the same as
"Begin Discussion" resolves `announce_death`'s pause.

Two new `FlowStatus` members in `bridge.py`:

```python
WAITING_FOR_VOTE = "waiting_for_vote"
VOTING_COMPLETE = "voting_complete"
```

`SessionBridge`'s outbox type comment (`DiscussionMessage | str |
FlowStatus`) gains `| VoteOutcome`.

## UI Changes (`ui.py`)

- Drop `from the_village.voting import ... cast_votes`; keep `VoteOutcome`
  for typing/rendering.
- `begin_voting_button.click` now takes `session_bridge` as an input,
  calls `bridge.resolve_input(PlayerInput())` (unblocking
  `run_discussion`'s new pause), and streams the bridge like
  `begin_discussion` does — reusing (or extending) `_stream_bridge` with
  two new cases:
  - `FlowStatus.WAITING_FOR_VOTE` → reveal the ballot (today's
    `begin_voting()` render: hide the button, show the vote row/candidate
    buttons).
  - A `VoteOutcome` item → render `format_vote_result` (unchanged) plus
    refreshed alive/lynched panels; followed by `FlowStatus.VOTING_COMPLETE`
    ending the stream (no further action — the Flow has finished, same
    terminal shape `DISCUSSION_COMPLETE` has today).
- Candidate/Abstain buttons switch from calling `cast_votes` synchronously
  to `bridge.resolve_input(PlayerInput(text=target))` followed by
  draining the bridge stream — mirroring `send_discussion_turn`'s shape —
  so "Tallying the votes…" shows immediately while the AI kickoffs run as
  part of the background Flow task rather than blocking the Gradio
  callback.
- `format_vote_result`, `format_alive_panel`, `format_lynched_panel`,
  `_vote_button_updates`, `_vote_candidate_names` are unchanged.

## Error Handling

- Same as discussion: any exception during `run_voting` is caught by
  `run_flow`'s existing broad handler and surfaces as `FlowFailed` on the
  outbox, rendered by `ui.py`'s existing `gr.Error` handling in the bridge
  stream — no new error path needed.
- Invalid AI vote targets (self, dead, unrecognized, unset) normalize to
  abstain via `_resolve_target`, same as today — no crash, no retry.

## Testing

- `tests/test_voting.py` moves to the package, split along the same
  lines as `discussion/`'s tests: `_Voter._resolve_target` cases,
  `_AiVoter` prompt-construction (mocked `Crew.akickoff`, same
  `ScriptedVoteAgent`-style fakes the current tests use, adapted for
  `akickoff`), and `Voting`-level tally tests (majority lynch, tie, all
  abstain, dead villagers excluded, player vote taken directly from the
  bridge) — all of today's `test_voting.py` cases carried over onto the
  new shape.
- `tests/test_flow.py` gains a case driving `VillageFlow` through
  `run_discussion`'s new pause (resolve it, like the existing tests do
  for `announce_death`), then through `_HumanVoter`'s
  `WAITING_FOR_VOTE` pause, asserting a `VoteOutcome` and
  `FlowStatus.VOTING_COMPLETE` land on the bridge in order.
- Manual pass in the browser: full day through to the vote reveal,
  covering a clear lynch and a tie, confirming the same UX beats as today
  (discussion → "Begin Voting" → ballot → pick → "Tallying…" → reveal).
