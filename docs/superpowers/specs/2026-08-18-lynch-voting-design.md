# Lynch Voting — Design

## Context

`the_village` is a single-player social-deduction game (Werewolf/Mafia
rules). The [night-one design](2026-08-14-werewolf-night-one-design.md)
covers roster setup and the first random kill; the
[day-one discussion design](2026-08-15-day-one-discussion-design.md)
covers the interactive discussion that follows and explicitly scoped
"lynch voting / elimination based on the discussion" out as future work.
This design covers that next step.

Scope for this design:

1. After a day's discussion completes, every living villager (the human
   player, AI villagers, and AI werewolves alike) votes for exactly one
   other living villager to lynch, or abstains.
2. Votes are cast without anyone knowing how others voted; the full
   breakdown (who voted for whom, and the tally) is revealed only once
   everyone has voted.
3. Whoever receives the most votes is lynched. A tie for the most votes,
   or no votes cast at all, means no one is lynched.
4. The Gradio UI changes needed to trigger voting, let the player cast
   their vote, and show the reveal.

Explicitly out of scope (future work):

- The day 2+ game loop itself (looping discussion → vote → night
   repeatedly, and win-condition checks) — only today's data model is
   shaped to support it, same precedent the discussion design set for
   itself.
- Any werewolf-specific vote coordination beyond what their existing
  discussion persona already implies (see "Voting AI" below).
- Persisting `GameState` across server restarts — in-memory for the
  session only, same durability `GameState` already has.

## Why plain Python orchestration, not a Flow step

Same reasoning the night-one and discussion designs already established:
tallying votes and applying the tie rule is deterministic bookkeeping,
not something that benefits from a `Crew`/`Flow` step, while each
villager's individual vote choice is genuine LLM reasoning and earns a
real `Agent.kickoff()` call. Voting is driven directly by the Gradio UI
as a new module, `voting.py`, invoked after a day's discussion has
completed — the same relationship discussion.py has to night.py.

Unlike discussion, voting does not need a turn-by-turn generator. Votes
are cast in secret and revealed all at once, so there's no pausing
mid-step for player input to interleave with AI turns — one function
collects every vote (the player's, passed in directly, plus one
`agent.kickoff()` per living AI villager) and returns the fully resolved
outcome in a single call.

## Data Model

Two new models added to `state.py`, alongside `Death`:

```python
class VoteRecord(BaseModel):
    day_number: int
    voter: str
    target: str | None = None  # None = abstained

class Lynching(BaseModel):
    name: str
    day_number: int
```

Both are added to `GameState`:

```python
class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1
    villagers: list[Villager] = []
    deaths: list[Death] = []
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    lynchings: list[Lynching] = []
```

`Lynching` is deliberately kept separate from `Death` rather than reusing
it with an added "cause" field: `Death` and its UI
(`DEATH_MESSAGE_TEMPLATES`, the "Killed by Werewolves" panel) are
specifically shaped around werewolf-attack flavor text, and a lynching is
a distinct kind of event narratively (public, voted, daytime) as well as
mechanically. Keeping them as separate lists means neither's formatting
logic needs a conditional branch for the other's story.

`votes` accumulates across the whole game (not just the current day),
mirroring how `discussion` and `deaths` already do — so a future
multi-day loop can let agents recall prior days' vote history if desired,
without a data model change.

## Vote Resolution (`voting.py`)

```python
class VoteChoice(BaseModel):
    target: str | None = None

class VoteOutcome(BaseModel):
    day_number: int
    votes: list[VoteRecord]
    tally: dict[str, int]
    lynched: str | None

def cast_votes(
    state: GameState,
    agents: dict[str, Agent],
    player_vote: str | None,
    rng: random.Random | None = None,
) -> VoteOutcome:
    ...
```

- `agents` is the same `dict[str, Agent]` already built by
  `start_discussion()` for the day — passed straight through from the
  `DiscussionRunner` the UI is already holding in `gr.State`. Reusing
  these `Agent` objects means each werewolf's "blend in and deflect
  suspicion" backstory and each villager's persona carry over into their
  vote without rebuilding anything.
- For each living AI villager, build a prompt containing: known facts
  (deaths and lynchings so far), the full multi-day discussion transcript
  (all of `state.discussion`, not just today — same `_format_history`
  helper `discussion.py` already uses, unfiltered by day), and the list
  of other living villagers they may vote for. Call
  `agent.kickoff(prompt, response_format=VoteChoice)`.
- `target` is validated the same way `discussion.py`'s `_resolve_target`
  validates `addressed_to`: it must name a living villager other than the
  voter; anything else (self-vote, a dead villager, an unrecognized name,
  or unset) is normalized to abstain (`target=None`). No retry, no crash
  — consistent with the discussion design's precedent for defensive
  handling of LLM output.
- The player's vote is taken directly from the `player_vote` argument
  (already chosen via UI button click) — no `kickoff()` call for it.
- One `VoteRecord` is appended to `state.votes` per living villager
  (the player included), all stamped with the current `state.day_number`.
- Tally: count non-abstain votes per target among all recorded votes.
  - No non-abstain votes at all → `lynched = None`.
  - A single target with the strictly highest count → that villager is
    lynched: `is_alive = False` on their `Villager`, a `Lynching`
    appended to `state.lynchings`, and `lynched` set to their name.
  - Two or more targets tied for the highest count → `lynched = None`
    (no one is lynched).

### Voting AI

Werewolves use the exact same `Agent` instance built for discussion, with
no additional pack-coordination logic layered on for voting. Their
existing backstory already establishes that they want someone else
blamed and actively steer suspicion during discussion; voting is just
another `kickoff()` call against that same persona reasoning over the
same transcript, so their vote naturally tends to follow whatever
suspicion they've already been steering — without the game hand-holding
werewolves into avoiding each other or piling onto the most-accused
villager. This mirrors the "prompt-level, not code-enforced" persona
approach the discussion design already used for villagers' and
werewolves' speaking behavior.

## Gradio UI (`ui.py`)

Additions to the existing result screen:

- When discussion status is `complete`, a "Begin Voting" button appears
  (same pattern as "Begin Discussion").
- Clicking it reveals the vote-casting controls: a row of pre-allocated
  button slots sized to the maximum possible non-player villager count
  (6, per `roster.py`'s `VILLAGER_NAME_POOL` sample), each round relabeled
  to a currently-living AI villager's name and hidden if there's no
  living villager left to fill that slot — plus one static "Abstain"
  button. This is a ballot for the *player* to cast their own vote;
  clicking a villager's button casts the player's vote for that person.
  AI villagers never see or interact with these buttons — their votes are
  decided independently via `cast_votes()`.
- Clicking any of those buttons immediately hides the voting controls and
  shows a brief "Tallying the votes…" status, then calls `cast_votes()`
  synchronously (blocking on each AI villager's `kickoff()` call — same
  blocking pattern `start_game` already uses for `flow.kickoff()`), then
  renders the reveal:
  - Full breakdown: one line per `VoteRecord`, e.g. `"Alice voted for
    Bruce."` / `"Corin abstained."`
  - Tally counts per target.
  - Outcome line: `"<name> was lynched by the village."` /
    `"The vote was tied — no one was lynched."` / `"No one voted to lynch
    anyone — no one was lynched."`
  - Updates the living-villagers panel (`format_alive_panel`, unchanged —
    it already reads `villager.is_alive`) and a new "Lynched by the
    Village" panel, chip-styled the same as the existing "Killed by
    Werewolves" panel, sourced from `state.lynchings`.

## Error Handling

- An AI vote naming a non-living-villager, the voter themself, or
  otherwise unparseable → normalized to abstain; the vote is still
  recorded. No crash, no retry.
- Any exception raised while casting/tallying votes is caught at the
  Gradio callback boundary and shown as a generic "something went wrong,
  please try again" message — same pattern `start_game` and the
  discussion handlers already use.

## Testing

New `tests/test_voting.py`, matching the fixture/mocking style of
`test_night.py` and `test_discussion.py` (seeded `GameState` fixtures,
fake agents via `SimpleNamespace` with a mocked `.kickoff`):

- A clear majority of votes for one villager → that villager is lynched,
  `is_alive` becomes `False`, a matching `Lynching` is appended.
- Two targets tied for the highest vote count → no lynch; no villager's
  `is_alive` changes.
- Every living villager abstains → no lynch.
- An AI vote targeting itself, a dead villager, or an unrecognized name
  → normalized to abstain rather than crashing or counting.
- Dead villagers are excluded both from casting a vote and from being a
  valid target.
- The player's vote is taken directly from `player_vote` with no
  `kickoff()` call involved for it.
- A `VoteRecord` is appended for every living villager (player included),
  each stamped with the current `state.day_number`.
- The prompt passed to each agent's `kickoff()` includes the full
  multi-day `state.discussion` history, not just the current day's
  messages (mocked agent — assert on the constructed prompt, not real
  output).
- Manual pass in the browser: run a full day (discussion → "Begin
  Voting" → cast a vote) through to the reveal, covering both a clear
  lynch outcome and a tie.
