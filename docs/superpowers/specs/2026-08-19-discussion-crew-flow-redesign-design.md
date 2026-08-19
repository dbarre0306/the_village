# Discussion Redesign: Crews + Flow — Design

## Context

The [day-one discussion design](2026-08-15-day-one-discussion-design.md)
built `discussion.py` as hand-rolled Python orchestration: a `DiscussionRunner`
dataclass, a generator-based `advance()` stepping function, and direct
`Agent.kickoff()` calls per turn. It explicitly rejected `Crew`/`Flow` for
this, reasoning that turn-taking bookkeeping (budgets, pass tracking, bonus
replies) didn't fit a `Crew`'s `Process`, and that `Flow` steps run to
completion in one `kickoff()` with no way to pause for a Gradio request.

This design replaces that approach. The goal now is explicitly to *use*
`Crew`s and a `Flow` for the discussion mechanism, as a deliberate exercise
in the framework's real orchestration primitives rather than working around
them — while keeping the scope bounded to what today's design covers:

1. Two discussion rounds per day, where every living participant (AI
   villagers, AI werewolves, and the human player) gets the option to speak
   or decline in each round.
2. A message that addresses someone directly (a question or accusation)
   still earns that person an out-of-turn bonus reply, which can itself
   chain further, exactly as today.
3. The whole thing runs asynchronously end-to-end so Gradio can display
   each message as it's produced, and so multiple people can play the game
   concurrently without one player's discussion blocking another's.
4. The final output of a day's discussion is the full transcript — a
   `list[DiscussionMessage]` — same shape as today.

Explicitly out of scope:

- Lynch voting (`voting.py`) is untouched. It continues to be invoked
  directly by `ui.py`, exactly as it works today, outside of any `Flow`.
- The day 2+ discussion *loop* (running this whole mechanism again on a
  later day) — this design covers a single day's two-round discussion; nothing
  here blocks reusing it for later days.
- Cleaning up an abandoned background task if a player closes their browser
  tab mid-game. This is a single-player-per-session local game, not a
  production multi-tenant deploy; an orphaned `asyncio.Task` for a
  disconnected session is an accepted cost, not something this design adds
  machinery for.

## Why Crews + a Flow this time

The previous design's objection to `Crew`/`Flow` no longer holds, for two
reasons specific to what changed:

- **`Flow` steps can pause for external input.** The earlier design assumed
  `human_input=True` (blocking on terminal `input()`) was the only pause
  mechanism `Flow` offered, and rejected it as a bad fit for a web request/
  response cycle. It isn't the only mechanism: an `async def` `Flow` step can
  simply `await` a plain `asyncio.Future`, which a Gradio event handler
  resolves later. CrewAI's own `@human_feedback` decorator (verified via
  `docs.crewai.com`) confirms pause/resume is a first-class supported
  pattern — that decorator's shape (LLM-classified approve/reject/revise
  feedback) doesn't fit a raw chat turn, so this design builds the simpler
  `Future`-based version directly rather than bending `@human_feedback` to
  a use it's not shaped for.
- **The turn-taking bookkeeping got simpler.** Dropping the multi-round
  budget system (see below) in favor of a fixed two rounds removes most of
  what made the old design's control flow deterministic-and-fiddly. What's
  left — build an order, walk it, handle bonus replies — maps cleanly onto
  a `Flow` step that loops, calling a small `Crew` per utterance.

Each single utterance (a scheduled turn or a bonus reply) becomes its own
`Crew` run — this is the unit where `Crew` earns its place: a real
multi-agent, multi-task collaboration (the speaker producing a message, the
Conversation Analyst reading it) rather than a single agent call.

## Data Model

`state.py` changes:

- `DiscussionMessage` is unchanged.
- `TurnOutput` drops `addressed_to` — addressing is now determined
  uniformly for every speaker (AI or player) by the Conversation Analyst's
  own task, not guessed by the speaker in the same call that produces their
  message.
- The budget system is removed entirely: no `INITIAL_BUDGET`, no
  `budgets: dict[str, int]`, no `passed: set[str]`. Two fixed rounds replace
  it — see below.

```python
class TurnOutput(BaseModel):
    has_something_to_say: bool
    message: str | None = None
```

`GameState.discussion` keeps its role as the durable, cross-day transcript
that future days' agents will build context from — unchanged from the
existing design.

## Agents & the turn-Crew

Built once per day's discussion, reused across every turn:

- One CrewAI `Agent` per living AI villager/werewolf (existing
  `_build_agent` logic, unchanged: role/goal/backstory branch on
  `player_type`, a werewolf's backstory includes their packmate's name).
- One shared Conversation Analyst `Agent` (existing backstory/prompt,
  unchanged) — the one agent that determines who, if anyone, a message
  addresses.

A single utterance is a `Crew` with `process=Process.sequential`:

- **AI speaker** (villager or werewolf): `Crew(agents=[speaker, analyst],
  tasks=[speak_task, analyze_task])`. `speak_task` asks the speaker for a
  `TurnOutput` (has something to say? if so, what?). `analyze_task`
  automatically receives `speak_task`'s output as context (native
  sequential chaining — no manual prompt-stitching needed) and returns
  `AddressResolution`.
- **Player speaker**: the message already exists (typed in Gradio), so this
  is just `Crew(agents=[analyst], tasks=[analyze_task])` run directly
  against their text — same `AddressResolution` output, same code path the
  AI case feeds into after its `speak_task`.

Both call `await crew.akickoff(inputs=...)` — the true-async path (see
Concurrency, below) — never `crew.kickoff()` or `crew.kickoff_async()`.

When a speaker declines (`has_something_to_say=False`), `analyze_task`
still runs — its prompt handles "there's nothing to analyze" by leaving
`addressed_to` unset. This costs one redundant LLM call per decline in
exchange for a single uniform two-task shape for every AI turn; worth
revisiting later if that cost matters, but not a reason to special-case the
`Crew`'s task list now.

## `DiscussionFlow`

A new `Flow[GameState]`, constructed with the shared `SessionBridge` (see
below) and a reference to the day's `GameState`. Runs exactly two rounds.

Each round:

1. Build the round order: every living participant (AI + player), shuffled.
   Apply the existing "avoid immediate repeat" swap against the *previous*
   utterance's speaker (tracked across the whole day, not reset between
   rounds — so round 2 doesn't open with whoever closed round 1).
2. Walk the order. For each name: run their turn-Crew (via the player-input
   bridge if it's the player's turn — see Async Execution). Append the
   resulting `DiscussionMessage` directly to `game_state.discussion` (same
   "no separate transcript to copy over" convention as the current design)
   and push it onto the bridge's outbox queue.
3. If the message resolved an `addressed_to`, resolve the bonus-reply chain
   before continuing the main order: run a turn-Crew for whoever was
   addressed (or, if that's the player, pause for their reply the same way
   a scheduled player turn does), and repeat if *that* reply itself
   addresses someone new — guarded by the same `chain: frozenset[str]`
   cycle-tracking the current design uses, so a chain terminates instead of
   ping-ponging forever.
4. Declining in round 1 does not exclude a participant from round 2 —
   everyone living gets one opportunity per round, two rounds total,
   independent of what they did in the other round.

Terminal state: `game_state.discussion` holds the day's full transcript,
which is also this `Flow`'s return value.

All step methods are `async def` (required for the concurrency properties
below).

## `VillageFlow` — the top-level orchestrator

`VillageFlow` becomes the async orchestrator for the whole session, not
just setup+night:

```python
class VillageFlow(Flow[GameState]):
    def __init__(self, bridge: SessionBridge):
        super().__init__()
        self.bridge = bridge

    @start()
    def setup_game(self):
        ...  # unchanged

    @listen(setup_game)
    def run_night_one(self):
        ...  # unchanged

    @listen(run_night_one)
    async def announce_death(self):
        await self.bridge.outbox.put(DeathAnnounced(self.state.deaths[-1]))
        self.bridge.pending_input = asyncio.get_event_loop().create_future()
        await self.bridge.pending_input  # resolved by the "Begin Discussion" click

    @listen(announce_death)
    async def run_discussion(self):
        transcript = await DiscussionFlow(
            bridge=self.bridge, game_state=self.state
        ).kickoff_async()
        self.state.discussion = transcript
        await self.bridge.outbox.put(FlowStatus.DISCUSSION_COMPLETE)
```

`VillageFlow.kickoff_async()` itself is kicked off once, as a background
`asyncio.Task`, when the player clicks "Start Game" — not called
synchronously and thrown away the way `main.py` calls it today. It runs for
the session's entire setup → night → discussion lifecycle; voting begins
only after this task completes, driven by `ui.py` exactly as today.

## Async execution & the Gradio bridge

One object, built fresh per session and threaded through both `VillageFlow`
and `DiscussionFlow`, reused for *every* pause point (the death-announcement
gate, each scheduled player turn, each player bonus-reply):

```python
@dataclass
class PlayerInput:
    message: str | None = None  # None means "passed"

@dataclass
class SessionBridge:
    outbox: asyncio.Queue  # Flow -> UI: DiscussionMessage | FlowStatus
    pending_input: asyncio.Future[PlayerInput] | None = None  # UI -> Flow
    task: asyncio.Task | None = None
```

A `Flow` step that needs player input creates a fresh `pending_input`
`Future` and `await`s it; the Gradio handler that fires when the player acts
resolves that `Future` with a `PlayerInput` and returns. The
"Begin Discussion" gate reuses the same `Future` mechanism with an empty
`PlayerInput()` — there's no message to carry, just a signal to unblock.

`ui.py`'s handlers become `async def` generators: they either start the
`VillageFlow` background task (`asyncio.create_task(...)`, on "Start Game")
or resolve `bridge.pending_input`, then loop `await bridge.outbox.get()`,
yielding a transcript/UI update for each item, until a status marker tells
them to stop and show the right controls (the input row, the "Begin
Discussion"/"Begin Voting" button, etc.) — the same incremental-yield
pattern `ui.py` already uses today, just sourced from the queue instead of
directly from `advance()`.

**`gr.State` discipline:** `SessionBridge` (and `VillageFlow`/
`DiscussionFlow` instances) are never passed as a `gr.State()` default
value — Gradio requires default values to be deep-copyable, and live
`asyncio` primitives aren't. They're built inside a handler (`start_game`)
and returned as an output, exactly the pattern `discussion_runner_state`
already uses today for the `Agent`-holding `DiscussionRunner`.

## Concurrency for multiple simultaneous players

Verified directly against CrewAI's source (`crewai/flow/runtime/
__init__.py`) and docs, plus Gradio's docs, since this matters for more than
one person playing at once:

- **`Flow.kickoff_async()` is natively async**, not thread-wrapped — `Flow`'s
  own synchronous `kickoff()` is the wrapper (it spins up `asyncio.run` in a
  thread for sync-context callers). Inside a running flow,
  `_execute_method` awaits `async def` steps directly on the event loop;
  only plain `def` steps get dispatched to a thread pool. This is why every
  `VillageFlow`/`DiscussionFlow` step here is `async def` — many concurrent
  games can share Gradio's event loop without each consuming a worker
  thread for its entire lifetime.
- **`Crew.akickoff()`, not `Crew.kickoff_async()`.** CrewAI's docs confirm
  `kickoff_async()` is `asyncio.to_thread` around the synchronous path and
  explicitly call it out as scaling poorly under concurrent load;
  `akickoff()` is true async/await throughout and is what's recommended for
  concurrent workloads. Every turn-`Crew` call in this design uses
  `akickoff()`.
- **Dropping the shared `concurrency_id`.** Gradio's docs confirm
  `concurrency_id`/`concurrency_limit` is a pool shared *globally across all
  users* of that id, not scoped per session. Today's code sets
  `concurrency_id="discussion_turn"` on the begin/send/submit/pass
  handlers — meaning, right now, only one discussion-turn handler call can
  run at a time across *every* concurrent player, an existing bug this
  redesign fixes rather than carries forward. Replacement: each handler
  checks whether this session's `bridge.pending_input` is already set/
  pending before acting, and no-ops otherwise (mirroring the existing
  "empty textbox is a no-op" pattern) — the same double-click protection,
  scoped correctly to one session instead of the whole app.

## Gradio UI (`ui.py`)

- `start_game` starts the `VillageFlow` background task instead of calling
  `flow.kickoff()` synchronously, then streams from the bridge's outbox
  until the death-announcement pause, updating the result screen the same
  way `begin_discussion` streams AI turns today.
- `begin_discussion` (renamed conceptually, same button) resolves
  `bridge.pending_input` to unblock `announce_death`, then resumes
  streaming from the outbox through both discussion rounds.
- `send_discussion_turn` / `pass_discussion_turn` resolve
  `bridge.pending_input` with the player's message/pass, same as today,
  now waking a paused `DiscussionFlow` step instead of resuming a Python
  generator.
- Transcript formatting, the "typing" placeholder pacing, and the pinned
  living/dead panels are unchanged — this redesign is about what drives the
  stream, not how it's rendered.
- Voting wiring (`begin_voting`, `cast_player_vote`, `cast_player_abstain`)
  is untouched.

## Error Handling

- Same posture as the existing design: an exception during a `Crew`
  run or inside a `Flow` step is caught at the Gradio callback boundary and
  surfaced as a generic "something went wrong, please try again" `gr.Error`,
  not allowed to crash the session.
- A malformed/unparseable `addressed_to` (names a non-participant, or fails
  to parse) is treated as `addressed_to=None` — recorded, no bonus reply,
  no retry — unchanged from today.
- If an exception occurs while the background `VillageFlow` task is
  running (not inside a Gradio callback), it must still reach the player:
  the task should catch it, push an error marker onto `bridge.outbox`, and
  let the consuming `ui.py` generator turn that into the same `gr.Error`
  path used for in-callback failures.

## Testing

- `DiscussionFlow`'s round-builder: across seeded-RNG trials, the previous
  utterance's speaker never opens the next round (except the unavoidable
  single-active-participant case) — same property the current design tests,
  now also verified across the round 1 → round 2 boundary.
- Two-round bookkeeping: everyone living gets exactly one turn per round,
  two rounds total, regardless of declining in either round.
- The bonus-reply chain: cycle-guarded via `chain`, including the case
  where the addressed party is the player (pauses) and where it's an AI
  (runs immediately).
- Turn-`Crew` construction: given a mocked `speak_task` output, the
  `analyze_task` receives it as context and its result becomes the
  message's `addressed_to` — assert on the `Crew`/`Task` wiring, not on
  real generated text.
- `SessionBridge` behavior: a second "send"/"pass" call while
  `pending_input` isn't set (or is already resolved) is a no-op, not a
  crash or a lost message.
- A concurrency test/smoke-check: two `VillageFlow` instances, each with
  their own `SessionBridge`, running as concurrent `asyncio.Task`s against
  mocked `Crew.akickoff`, complete independently without interleaving each
  other's transcripts.
- `Crew`/`Agent`/LLM calls are mocked throughout, same as the current
  design — assertions target orchestration (round structure, addressing,
  pause/resume), not generated text quality.
- Manual pass in the browser with two browser sessions open concurrently:
  confirm one session's discussion doesn't block or interleave with the
  other's.

## Open Questions

- Should the redundant `analyze_task` call on a decline (see Agents &
  the turn-Crew) be optimized away now via a conditional task, or left as
  documented, simple, slightly wasteful behavior for this pass?
- `main.py`'s current `kickoff()`/`plot()` CLI entry points call
  `VillageFlow` synchronously for local testing outside Gradio — these need
  an async-compatible replacement (or an `asyncio.run()` wrapper) once
  `VillageFlow` itself becomes async-native.
