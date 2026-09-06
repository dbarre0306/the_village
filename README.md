# The Village

The Village is a Werewolf/Mafia-style social-deduction game built with
[CrewAI](https://crewai.com). One human plays alongside six AI villagers,
some of whom are secretly werewolves. Play alternates between a werewolf
night-kill, a discussion phase, and a lynch vote, until one side wins. The
game is played through a Gradio UI; a headless CLI run also exists as a
smoke test.

## Installation

Ensure you have Python >=3.10 <3.14 installed on your system. This project
uses [uv](https://docs.astral.sh/uv/) for dependency management and package
handling, offering a seamless setup and execution experience.

First, if you haven't already, install uv:

```bash
pip install uv
```

Next, navigate to the project directory and install the dependencies:

```bash
uv sync
```

Add `OPENAI_API_KEY` (and `MODEL`) to a `.env` file at the project root.

## Running the Project

- `uv run app` — launches the real Gradio UI. This is how you actually play
  the game.
- `crewai run` (alias: `uv run kickoff`) — a headless CLI run of the full
  Flow. There's no live UI to answer pauses, so every AI/human turn
  auto-declines or auto-abstains; this drives the game to completion as a
  smoke test, not a way to actually play.
- `uv run pytest` — runs the unit test suite (`tests/`, 300+ tests). Tests
  marked `integration` (hit a real LLM — slow, costs tokens) are excluded by
  default; run them explicitly with `uv run pytest -m integration`.
- `crewai test` evaluates agent output quality against real LLM calls; it is
  not the pytest suite above.

## Architecture

### The Flow

The game itself is one long-lived crewAI **Flow**, not a Crew that runs
start to finish. The Flow owns a single pydantic game state — player
roster, the day's discussion transcript, votes, and deaths — and every
agent prompt is a markdown rendering of that state, so what an agent
"knows" is always exactly what the Flow has recorded so far. The Flow steps
through:

```
setup game (roster + roles)
  → night-one kill (unconditional random target, no agent involved)
  → announce the death
  → a day of discussion (fixed speaking rounds, human included)
  → a lynch vote (human votes first)
  → winner check
  → next night's kill (a real werewolf decision, see below)
  → repeat until one side wins
```

A background asyncio task runs the Flow while the Gradio UI drives it
through a small message-passing bridge: an outbox queue carries state
updates out to the screen, and a single reusable "waiting for an answer"
future is how a paused step blocks on the next human input (a chat message,
a vote, an accept/decline) without the UI and the Flow ever touching each
other's internals directly.

### The Crews

The Flow does not run everything through one big Crew. Instead, every
point where an agent has to actually decide something spins up a small,
disposable Crew just for that decision, gets an answer back, and throws the
Crew away. There's no persistent Crew object and no YAML config — agents
and tasks are built in code at the point of use. Four such decisions exist:

- **Speaking.** Each living player's turn to talk is a one-agent,
  one-task Crew whose task returns structured output (does this player
  want to speak, what do they say, who — if anyone — are they addressing).
  That output is checked by an **LLM guardrail** before it's accepted, and
  the guardrail deliberately runs on a _different, stronger model_ than the
  one generating the line — judging whether a statement is properly
  grounded turned out to need more reasoning fidelity than the cheap model
  used for flavor dialogue could reliably deliver. A failed guardrail
  triggers one retry; if it still fails, that player's turn is silently
  skipped rather than blocking the round.
- **Voting.** Each living player's vote is likewise a one-agent, one-task
  Crew returning a structured pick (or an abstain). There's no guardrail
  here — an invalid or unresolvable vote is just discarded and logged
  rather than retried, since a missing vote doesn't stall the game the way
  a stuck discussion turn would.
- **The night kill.** This is the one multi-agent Crew. Every living
  werewolf's "who should we kill" task is marked to run **concurrently**
  (`async_execution`), and the pack leader's task is wired to depend on all
  of their outputs as context — so the leader effectively waits for the
  whole pack to weigh in, then makes the actual call, guarded to only
  accept a living, non-werewolf target with one retry. Because a kill must
  happen every night no matter what, any Crew failure or exhausted
  guardrail retry falls back to picking a random eligible target rather
  than stalling the game.
- **Addressing.** A separate single-purpose "conversation analyst" agent —
  not tied to any player — judges whether a just-spoken message is
  directly addressed to another living player (versus merely mentioning or
  accusing them). When it is, that triggers a short reply chain before the
  discussion round continues.

## Support

For support, questions, or feedback regarding CrewAI:

- Visit the [documentation](https://docs.crewai.com)
