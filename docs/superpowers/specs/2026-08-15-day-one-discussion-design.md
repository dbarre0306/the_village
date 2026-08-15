# Day-One Discussion — Design

## Context

`the_village` is a single-player social-deduction game (Werewolf/Mafia
rules). The [night-one design](2026-08-14-werewolf-night-one-design.md)
covers roster setup and the werewolves' first random kill, and explicitly
scoped day-phase discussion out as future work. This design covers that
next step: the interactive discussion that follows the night-one death.

Scope for this design:

1. An interactive, turn-based discussion among the 6 surviving villagers
   (4 non-werewolf AI villagers + 2 werewolf AI villagers) and the human
   player, reacting to the night-one death.
2. The human player participates in real time — typing messages, asking
   or answering questions, optionally passing.
3. The Gradio UI changes needed to drive this turn by turn.
4. Discussion history is preserved on `GameState` for the lifetime of the
   game session, so that when future days/nights are built, villagers can
   recall what was discussed on prior days.

Explicitly out of scope (future work):

- Lynch voting / elimination based on the discussion.
- The day 2+ discussion loop itself (the UI/orchestration to actually run
  a second day) — only today's data model is shaped to support it.
- Persisting discussion (or any `GameState`) across server restarts —
  "preserved throughout the game" means in-memory for the session, the
  same durability `GameState` already has; it does not mean durable
  storage.
- Automated hallucination/fact-checking guardrails beyond prompt
  instructions.

## Why plain Python orchestration, not a Flow step or Crew process

The night-one design established the precedent of using plain Python where
deterministic control logic is a better fit than a CrewAI `Crew`/`Flow`
step, and only reaching for LLM reasoning where a persona genuinely needs
to reason. Discussion is the opposite case from night one: there **is**
real reasoning to do (each villager forming and voicing suspicion), but the
turn-taking itself (whose turn, budgets, question interrupts, stop
condition, round-boundary constraints) is deterministic bookkeeping that a
`Crew`'s own `Process` (sequential/hierarchical) is not built to express —
hand-rolling it in Python gives full control over the rules below.

This also means discussion does not fit naturally as a `VillageFlow`
`@listen` step: `Flow` steps model a linear pipeline that runs to
completion in one `kickoff()`, but discussion needs to pause mid-step,
return control to the Gradio UI for player input, and resume — potentially
many times. CrewAI's `human_input=True` mechanism exists for this kind of
pause, but it is built around blocking on terminal `input()`, not a web
request/response cycle; bending it to Gradio would fight the framework
harder than it's worth. Instead, discussion is driven directly by the
Gradio UI as a new module, `discussion.py`, invoked *after* `VillageFlow`
has already completed (night one is already resolved by the time
"Begin Discussion" is clickable).

Each AI villager is a real CrewAI `Agent` (the first genuine `Agent` usage
in the project), created once when discussion starts and invoked via
`agent.kickoff()` on each of its turns — this is where the project's
`Agent` construct earns its place, unlike night one's coin-flip kill.

## Data Model

New model, added to `state.py` alongside `Death` (a data shape, not
behavior — consistent with the existing split between `state.py` for
models and `night.py`/`roster.py` for logic):

```python
class DiscussionMessage(BaseModel):
    day_number: int
    speaker: str
    message: str
    addressed_to: str | None = None
```

`addressed_to` means "who this message specifically calls out" — set for
both direct questions ("Corin, where were you?") and accusations ("I think
it's Corin") alike. It is not restricted to grammatical questions.

`day_number` mirrors `Death.day_number` and records which day's discussion
this message belongs to.

Unlike the earlier draft of this design, `DiscussionMessage` **is** added
to `GameState`:

```python
class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1
    villagers: list[Villager] = []
    deaths: list[Death] = []
    discussion: list[DiscussionMessage] = []
```

Discussion needs to outlive a single day: villagers must be able to recall
what was discussed on prior days once a day 2+ loop exists. Putting it on
`GameState` — the same place `deaths` lives — makes it part of the
persisted game story rather than orchestration-only state, and future
days' agents build their context from this accumulated list rather than
from anything scoped to a single day's `DiscussionRunner`.

## Orchestration State (`discussion.py`)

`DiscussionRunner` is a plain Python class (not a `pydantic.BaseModel` —
it holds live CrewAI `Agent` objects, which don't belong in a serializable
data model):

```python
class DiscussionRunner:
    state: GameState                  # appends new messages directly to state.discussion
    agents: dict[str, Agent]          # AI villager name -> Agent
    budgets: dict[str, int]           # participant name -> remaining budget (starts at 3)
    passed: set[str]                  # participants permanently done
```

`DiscussionRunner` holds a reference to `GameState` and appends each new
`DiscussionMessage` straight onto `state.discussion` as it's produced —
there is no separate transcript to copy over at completion. `budgets` and
`passed` are the only state that's genuinely scoped to the *current* day's
in-progress round-robin (whose turn budget is left, who's permanently
passed today) and don't belong on `GameState`; they're rebuilt fresh each
time `start_discussion()` runs for a new day.

The runner itself is held in a Gradio `gr.State`, scoped to one browser
session, for the duration of the current day's discussion only — same
"no shared mutable state across sessions" property the night-one design
established for `GameState`.

`last_speaker`, used for the round-boundary constraint below, is not
separately tracked — it's derived on demand as the speaker of the last
entry in `state.discussion` where `day_number == state.day_number` (i.e.
scoped to today's messages only, so the constraint naturally resets at the
start of each new day rather than looking across the day boundary).

### Participants

All 6 surviving villagers plus the player (7 total). The night-one victim
is excluded — they're dead and don't participate.

### Turn budget and passing

- Each participant starts with a budget of 3 "initiating" turns (a
  statement, question, or accusation).
- A **round** = shuffle the currently *active* participants (budget > 0
  and not in `passed`), producing this round's speaking order.
- On a normal rotation turn, a participant either speaks (budget −1,
  optionally sets `addressed_to`) or **passes**. Passing on a normal turn
  is **permanent** — that participant is added to `passed` and takes no
  further normal turns for the rest of the discussion.
- Discussion ends when no participants remain active (all have hit budget
  0 or passed).

### Round-boundary speaker constraint

Whoever authored the most recent transcript entry (`last_speaker` —
including bonus replies, not just scheduled rotation turns) must not be
placed first in the next round's shuffled order. If the shuffle would put
them first and more than one participant is active, swap them into a
different slot. No-op if they aren't in the new round's active set, or if
they're the only active participant left (unavoidable).

### Questions and accusations (`addressed_to`)

If a message sets `addressed_to` (a question *or* an accusation naming
another living participant), that participant gets exactly **one**
immediate, free reply — no budget cost, jumps ahead of the normal
rotation — before the rotation resumes where it left off. If that reply
itself sets `addressed_to`, it does **not** chain into another bonus reply
— it's recorded normally, and whoever it names may pick it up on their own
next natural turn. This bounds the state machine and prevents back-and-forth
ping-ponging between two participants.

If the addressed participant is the player, `advance()` pauses (see below)
and waits for their reply. If the player passes here — declining to
respond to being asked/accused — that is **not** a permanent pass; they
remain active for future normal turns. This is the one place passing does
not remove a participant from the discussion.

Defensive handling: if a generated `addressed_to` names someone who isn't
a current living participant (parse issue, LLM naming error, or the
addressed target no longer active), treat it as `addressed_to=None` — the
message is still recorded, just without triggering a bonus reply. No
crash, no retry, matching the night-one design's precedent of not building
retry/guardrail machinery where it isn't earning its keep.

## Turn Generation (AI villagers)

Each living AI villager gets a CrewAI `Agent`, created once in
`start_discussion()`, with a backstory built from:

- Their name and persona.
- Their own role (villager or werewolf) — never revealed to others unless
  they choose to say so in the discussion.
- If a werewolf: their pack's identity (the other werewolf's name). Plain
  villagers do not know who the werewolves are.

Each turn is a `Task` with `output_pydantic` against:

```python
class TurnOutput(BaseModel):
    has_something_to_say: bool
    message: str | None = None
    addressed_to: str | None = None
```

`has_something_to_say=False` is how an AI villager passes (see budget
rules above). The `Task` description includes all of `state.discussion`
— every prior day's messages plus today's so far, in `day_number` order —
and the two persona instructions below. Passing the full accumulated
history (not just today's) is what lets villagers reference what was said
on earlier days once a day 2+ loop exists; day one itself, this list is
just today's messages.

**Persona instructions** (prompt-level, not code-enforced — consistent
with keeping this build simple, same reasoning the night-one design used
to justify no guardrails where an LLM call has nothing complex to
validate):

- Speak like a person reacting to a death in their community — shock,
  grief, anger, suspicion are natural and expected. Not flat recitation.
- Ground every statement strictly in: (a) the discussion history provided
  (all days, oldest first), (b) the public fact of who died and when,
  (c) their own private knowledge (their own role; a werewolf also knows
  their pack). Never invent facts, alibis, or claims not present in that
  context.

## `advance()` — the stepping function

```python
def start_discussion(state: GameState) -> DiscussionRunner: ...

def advance(
    runner: DiscussionRunner,
    player_input: str | None = None,
    player_pass: bool = False,
) -> Iterator[DiscussionMessage | AdvanceStatus]:
    ...
```

`advance()` is a generator. It auto-plays AI turns — yielding each new
`DiscussionMessage` as it's produced — until either the player must act or
the discussion is complete, then yields a final `AdvanceStatus`:

- `waiting_for_turn` — it's the player's normal rotation turn.
- `waiting_for_answer` — the player was just addressed (question or
  accusation) and owes a reply.
- `complete` — no participants remain active.

The UI's click handlers are themselves generators that iterate `advance()`
and update the transcript panel after every yielded message, so the player
watches the discussion unfold rather than seeing one large jump — this is
what satisfies "real time" without needing token-level LLM streaming.

## Gradio UI (`ui.py`)

Additions to the existing result screen:

- `gr.State` for `GameState` — this needs to survive from the "Start Game"
  click into later clicks; today nothing persists `flow.state` past the
  initial render, so this is a new requirement introduced by this
  feature.
- `gr.State` for `DiscussionRunner | None`.
- A "Begin Discussion" button, the only new visible control until clicked.
- A discussion transcript `gr.Markdown` panel, rendered as `"**Speaker:**
  message"` lines per entry — same formatting convention as the existing
  `format_event_log`.
- A player-input row: `Textbox`, an optional "Address to" `Dropdown` of
  currently living participant names, a "Send" button, and a "Pass"
  button. Hidden until it's the player's turn (either status), hidden
  again once discussion completes.

**Click wiring:**

- `Begin Discussion` → `start_discussion(game_state)` populates the
  `DiscussionRunner` state, then a generator handler iterates
  `advance(runner)`, yield-updating the transcript, until a pause/complete
  status arrives — then shows or hides the input row accordingly.
- `Send` / `Pass` → generator handler calls `advance(runner,
  player_input=..., player_pass=...)`, same incremental-yield pattern.

## Error Handling

- Malformed/unparseable `addressed_to` from an agent, or one naming a
  non-participant → treated as `addressed_to=None`; the message is still
  recorded. No crash, no retry.
- Any exception raised during `advance()` is caught at the Gradio callback
  boundary and shown as a generic "something went wrong, please try
  again" message — same pattern `start_game` already uses.

## Testing

- Unit tests for the round-builder: across seeded-RNG trials, the previous
  round's `last_speaker` never lands first in the next round's order
  (except the unavoidable single-active-participant case).
- Unit tests for budget/pass bookkeeping: a normal-turn pass is permanent;
  a pass while answering a question is not; budget decrements only on
  substantive normal turns, never on bonus replies.
- Unit tests for the `addressed_to` bonus-reply mechanic, including that a
  bonus reply's own `addressed_to` does not chain into a second bonus
  reply.
- Unit test that messages produced during discussion land on
  `state.discussion` with the correct `day_number`, and that a seeded
  `state.discussion` from a prior day is included, unmodified, in the
  context passed to agents on a later day (mocked agent — assert on the
  constructed `Task` input, not real output).
- Agent/LLM calls are mocked in tests — assertions target orchestration
  logic (turn order, budgets, stop condition), not generated text or
  persona quality, which isn't something a unit test can meaningfully
  verify.
- Manual pass in the browser: run a full discussion from "Begin
  Discussion" through completion, including at least one case of being
  addressed (question or accusation) and one case of passing on a normal
  turn.
