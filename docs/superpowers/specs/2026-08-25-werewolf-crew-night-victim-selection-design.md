# Werewolf Crew Night Victim Selection — Design

## Context

[Night one](2026-08-14-werewolf-night-one-design.md) deliberately made the
werewolves' first kill a plain `random.choice` in code
(`night.py::resolve_night_one`) — on night one there's no day-phase
history yet, so an LLM discussion would be theater. That design explicitly
flagged a `Crew`-based discussion as "worth reconsidering once a day phase
exists and werewolves have accusations/suspicion to reason about." Day-phase
[discussion](2026-08-19-discussion-crew-flow-redesign-design.md) and
[voting](2026-08-24-voting-flow-integration-design.md) now exist, so every
night after the first has real signal (who's been accused, who's been
lynched, what's been said) for werewolves to reason about.

This design adds that capability: a `Crew`-based kill decision for any
night after the first, usable repeatedly as the game progresses through
multiple day/night cycles. `resolve_night_one` is untouched — it stays the
random, no-LLM first-night pick.

## Scope

In scope:

- A new `resolve_night(state, player_agents, rng=None)` that runs a
  `Crew` of the living werewolves to pick a victim, usable on any night
  after the first, any number of times.
- Pack-leader succession in plain code when the current leader has been
  lynched.
- Victim eligibility broadened to include the human player, not just AI
  villagers.

Explicitly out of scope (deferred to later work):

- Wiring `resolve_night` into `VillageFlow`'s loop (repeating
  night → discussion → voting across multiple days).
- Win-condition detection / game-over state.
- Any UI change — the werewolves' reasoning is never shown to the player,
  same as night one.

## Why these boundaries

**Pack-leader succession is plain code, not an LLM call.** Who leads the
pack is bookkeeping — a flag on `Player` — not a judgment call requiring
reasoning. Resolving it in code before the `Crew` runs also guarantees the
`Crew`-building step always has a living leader to put last in the
ordering, with no separate "no leader present" branch to design or test in
the LLM-facing logic.

**One sequential `Crew`, not one `Crew` per werewolf.** Voting's `_AiVoter`
runs one `Crew` per voter because each vote is genuinely independent. A
kill decision isn't — the design goal is a pack discussing before
deciding. CrewAI's `context=` parameter gives each task the accumulated
output of every prior task in the same `Crew`, which is exactly "each
werewolf hears everyone who spoke before them," for any pack size, with no
manual transcript-passing or reply-chain machinery like `discussion/`
needs for its N-way public conversation.

**The pack leader decides, always informed by the full pack.** Ordering
non-leaders first (shuffled) and the leader last means the leader's task
always has `context=` covering every other living werewolf's reasoning —
full accumulated history, not just the immediately preceding speaker. With
one living werewolf, this collapses naturally to a single task with no
context: that werewolf's output is both the (only) reasoning and the
binding decision. No branching on pack size anywhere in the ordering or
`Crew`-building code.

**Guardrail, not just a Python fallback, for target validity.** Voting's
`_AiVoter` accepts an invalid/self vote and normalizes it to an abstain —
a vote is allowed to be nothing. A night kill isn't: a victim must always
be named. CrewAI's `guardrail` on the decision task lets the LLM retry
(default 3 attempts) with feedback naming the actual eligible targets when
it names someone invalid, which is a better failure mode than silently
substituting a random name for a coherent-looking but wrong LLM choice.
The `rng.choice` fallback still exists, but only as a last resort if the
guardrail exhausts its retries — mirroring night one's randomness as the
worst-case behavior, not the primary path.

**The victim pool includes the human player.** Night one deliberately
excludes the player (`player_type == "user"` is never in the
`"villager"`-only eligible set) because a random first kill hitting the
player on turn one would be an unwinnable, un-fun opener. Once werewolves
are reasoning about who to target using real discussion/voting history,
excluding the player from that reasoning has no such justification — they
are a living participant in the game like any villager, and should be a
valid target from night two onward.

## Package Structure

`night.py` becomes a `night/` package:

- **`night/night_one.py`** — today's `resolve_night_one`, moved as-is,
  unchanged. Still `random.choice` over living `"villager"`-type players,
  no LLM, no `player_agents` parameter.
- **`night/night.py`** — new `resolve_night`, covered below. No base
  class shared with `night_one.py` — the two functions don't share
  behavior worth abstracting, the same way `Voting` and `Discussion` don't
  share a base despite superficial similarity.
- **`night/__init__.py`** — exports both `resolve_night_one` and
  `resolve_night`.

Unlike `voting/` and `discussion/`, there's no `_Voter`/`_Speaker`-style
base class or AI/human split here: every werewolf is AI (`roster.py` never
assigns `player_type="werewolf"` to the human), so there's exactly one
kind of decision-maker to model.

## `resolve_night`

```python
async def resolve_night(
    state: GameState,
    player_agents: dict[str, Agent],
    rng: random.Random | None = None,
) -> GameState:
```

Note the async signature — a real difference from `resolve_night_one`,
since this calls `Crew.akickoff()`. Callers (eventually `VillageFlow`)
must `await` it.

1. **Ensure a living pack leader.** Find the werewolf currently flagged
   `is_pack_leader=True`. If they're not alive, `rng.choice` a
   replacement from the living werewolves, set `is_pack_leader=True` on
   the replacement, clear it on the old leader. This mutates
   `state.players` persistently — the new leader holds the role for
   future nights too, not just this call. Zero living werewolves is not
   a reachable state for this function (win-condition handling ends the
   game first, once that exists) and isn't guarded against here, the same
   way `night_one.py` doesn't guard an empty eligible list.
2. **Order the pack.** `living = shuffle(non_leader_living_werewolves,
   rng) + [pack_leader]`. Works uniformly for 1..N living werewolves; a
   single survivor produces a one-element list with no special case.
3. **Compute eligible targets.** `[p.name for p in state.players if
   p.is_alive and p.player_type != "werewolf"]` — every living villager
   *and* the human player, never a werewolf (packmates are never
   targets).
4. **Build the `Crew`.** One `Task` per werewolf in `living`, each
   `agent=player_agents[name]` (the same persona agent used for
   discussion/voting), each `context=` the full list of prior tasks in
   `living`'s order. Every task's prompt includes
   `state.format_deaths()`, `state.format_lynchings()`,
   `state.format_history()`, and the eligible-target list, the same
   known-facts shape `_AiVoter._build_vote_prompt` uses. Only the last
   task (the pack leader's) carries `output_pydantic=_VictimChoice`
   (`target: str | None`) and a `guardrail` requiring the target to be
   one of the eligible names — returning `(True, choice)` on success or
   `(False, "You must pick a living target from: ...")` to trigger a
   retry (default 3 attempts) with feedback. Earlier tasks are free-text
   reasoning only — no structured output, no guardrail — since they only
   need to feed `context=` into what follows, not produce a decision
   themselves.
5. **Run it.** `result = await Crew(agents=..., tasks=..., process=Process.sequential).akickoff()`.
6. **Resolve the victim.** On success, `result.tasks_output[-1].pydantic.target` is
   guardrail-guaranteed eligible. If the `Crew` run raises (guardrail
   retries exhausted, or any other failure) *or* completes but
   `tasks_output[-1].pydantic` is unexpectedly missing, log a warning and
   fall back to `rng.choice(eligible)`.
7. **Mutate state.** Mark the victim `is_alive = False`, call
   `state.advance_day(player_killed=victim_name)` — identical contract
   shape to `resolve_night_one`.

No `GameState`/`Player` schema changes are needed — `is_pack_leader`
already exists and was unused until now; `_VictimChoice` is a private
model local to `night/night.py`, the same way `_VoteChoice` is private to
`ai_voter.py`.

## Error Handling

- A werewolf naming an ineligible target (dead, self-targeting a
  werewolf, hallucinated name) triggers the guardrail's retry with
  feedback naming the actual eligible list — the same "tell the model
  what's actually valid" approach as `_AiVoter`'s prompt, but enforced as
  a hard retry loop instead of accepted-and-normalized.
- If the guardrail exhausts its retries (or the `Crew` run fails for any
  other reason), catch it around the `akickoff()` call, log a warning,
  and fall back to `rng.choice(eligible)` — a kill always happens, same
  guarantee `resolve_night_one` already provides.

## Testing

Mirrors `tests/voting/test_ai_voter.py`'s approach — `Crew.akickoff`
patched with `AsyncMock`, a stub `Agent` per werewolf, no real LLM calls:

- **Leader succession**: dead leader → a new one is chosen from living
  werewolves (seeded `rng`), flag moves correctly; living leader → no
  change.
- **Ordering**: non-leaders shuffled, leader always last, for pack sizes
  of 1, 2, and a larger N to confirm no hidden assumption about exactly
  two werewolves.
- **Context chaining**: each task's `context` contains exactly the prior
  tasks in order — leader's task sees the full accumulated pack, not just
  the immediately preceding one.
- **Eligibility**: prompt/guardrail candidate list includes living
  villagers and the human player, excludes werewolves and the dead.
- **Guardrail rejection**: a mocked result naming a werewolf or a dead
  player is rejected (guardrail returns `False`); a valid name is
  accepted.
- **Fallback**: a mocked `akickoff` that raises, and separately one that
  returns a missing/`None` final `pydantic`, each produce a logged
  warning and a target chosen from `rng.choice(eligible)`.
- **State mutation**: victim marked `is_alive = False`,
  `state.advance_day` called with the correct name, matching
  `resolve_night_one`'s existing contract tests.
- **`resolve_night_one` regression**: existing `tests/test_night.py`
  cases continue to pass unmodified against the moved-but-unchanged
  `night/night_one.py`, confirming night one's behavior didn't shift.
