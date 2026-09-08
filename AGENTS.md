# Application Description

`the_village` is a Werewolf/Mafia-style social-deduction game. One human
plays alongside six AI villagers, one of whom is secretly a werewolf. Play
alternates between a werewolf night-kill, a discussion phase, and a lynch
vote, until one side wins. AI players are CrewAI agents that speak, vote, and
choose kill targets by reasoning over a text summary of everything that has
happened in the game so far. The game is played through a Gradio UI
(`the_village.ui`); a headless CLI run also exists as a smoke test.

# Architecture

The project is a single CrewAI **Flow** (not a Crew), driven by
`VillageFlow` in `core/village_flow.py`. `GameState` (`core/state.py`) is
the flow's pydantic state and the single source of truth for the game:
player roster, per-day discussion transcript, votes, and deaths.
`GameState.known_facts()` renders that state into the markdown context every
agent prompt is built on — read it before changing what agents "know."

Flow steps (`@start`/`@router`/`@listen`) walk: `setup_game` → night-one kill
(`pick_victim/kill_first_victim.py`, unconditional random target) →
`announce_death` → `run_discussion` → `run_voting` → winner check → next
night's kill (`pick_victim/werewolf_pack.py`) → repeat, until
`GameState.determine_winner()` returns a winner.

- **`core/`** — `village_flow.py`, `state.py`, and `bridge.py`: the Flow
  orchestrator, its pydantic game state, and the UI↔Flow channel.
- **`core/bridge.py`** — `SessionBridge` is the only channel between the
  background Flow task and the Gradio UI: an `asyncio.Queue` (`outbox`)
  carries Flow → UI updates, and a reused `asyncio.Future` (`pending_input`)
  is how a paused flow step waits on one UI → Flow value at a time. Read the
  class docstring before changing pause/resume behavior — the reuse-per-session
  (rather than per-pause) design and the `voting_started` flag both exist to
  dodge specific double-fire/race bugs.
- **`agents/`** — `agent_factory.build_agent()` dispatches to
  `villager.py`/`werewolf.py` builders by `Player.player_type`. Werewolf
  agents are additionally told who their packmates are.
  `conversation_analyst.py` builds a separate, single-purpose Agent (owned
  by `VillageFlow`, not tied to any player) that judges who a discussion
  message addresses, feeding `discussion/speaker.py`'s address resolution.
- **`discussion/`** — `Discussion` runs a fixed number of speaking rounds per
  day in shuffled order (with rules against the same/human player opening
  cold). Each living player gets a `_Speaker` (base in `speaker.py`);
  `_HumanSpeaker` (`human_speaker.py`) subclasses it directly, while
  `_VillagerSpeaker`/`_WerewolfSpeaker` (their own files) share AI-only
  behavior via an intermediate `_AiSpeaker` base in `ai_speaker.py`. A
  message that addresses another player triggers `reply_chain.py` before
  the round continues.
- **`voting/`** — `Voting` casts votes in a fixed order (human always first,
  for UX reasons — see `_voting_order()`), one `_Voter` per living player
  (base in `voter.py`); `_HumanVoter` subclasses it directly, while
  `_VillagerVoter`/`_WerewolfVoter` share AI-only behavior via an
  intermediate `_AiVoter` base in `ai_voter.py`. Votes are tallied and a
  lynch resolved (no lynch on a tie). `pick_victim/werewolf_pack.py` follows
  the same per-role-subclass pattern for the nightly kill, plus a CrewAI
  `Task` guardrail that forces the choice onto a living, non-werewolf
  target, and a random fallback if the Crew run or guardrail retries fail —
  a kill must always happen.
- **`roster/`** — `roster.py`'s `build_initial_roster()` builds the initial
  7-player roster with `NUMBER_OF_WEREWOLVES` random werewolves (one of them
  flagged pack leader), each villager randomly assigned a flavor personality
  from `personalities.py`. `NUMBER_OF_WEREWOLVES` is currently `1` but is
  expected to change, so nothing outside `roster.py` may hardcode a specific
  werewolf count (one, two, or otherwise) — every other werewolf-count-
  dependent code path (agent backstories in `agents/werewolf.py`, the
  discussion/voting prompts, the nightly kill in `pick_victim/werewolf_pack.py`)
  must derive the count/names at runtime from `GameState` (`werewolf_names()`,
  `living_werewolves()`, and friends) so that changing the constant alone is
  enough to change the game.
- **`ui/`** — the Gradio app; `build_app()` owns a `SessionBridge` per
  session via `gr.State` and paces discussion messages onto screen
  independently of how fast the background Flow task produces them.
  The project root's `app.py` is the thin launch entrypoint that wires
  CSS/JS and calls `build_app().launch()` — it lives at the repo root
  (not under `src/the_village/`) because Hugging Face Spaces expects the
  Gradio entrypoint there, and it prepends `src/` to `sys.path` itself so
  `the_village` imports resolve without the package being installed.

`README.md` still describes the generic `crewai create flow` template
(`config/agents.yaml`, `config/tasks.yaml`, `crew.py`) — none of that exists
in this project; agent/task construction is done in code, per the CrewAI
rule below.

## CrewAI

Will use the jsonc approach when possible. If not possible, then default to code. Never use yaml files.

# Running and Testing

Requires `OPENAI_API_KEY` (and `MODEL`) in `.env`. Uses
[uv](https://docs.astral.sh/uv/) for dependency management — prefix commands
with `uv run` if the venv isn't already activated.

- `crewai run` (alias: `uv run kickoff`) — headless CLI run of the full Flow.
  There's no live UI to answer pauses, so every AI/human turn auto-declines
  or auto-abstains (`core.village_flow._auto_play_consumer`); this drives the
  game to a `GameOverResult` as a smoke test, not a way to actually play.
- `uv run python app.py` — launches the real Gradio UI (root `app.py`).
- `uv run pytest` — runs the unit test suite (`tests/`, 300+ tests). Tests
  marked `integration` (hit a real LLM — slow, costs tokens) are excluded by
  default via `addopts` in `pyproject.toml`; run them explicitly with
  `uv run pytest -m integration`.
- `uv run pytest tests/discussion/test_ai_speaker.py::test_name` — run a
  single test.
- `crewai test` evaluates agent output quality against real LLM calls; it is
  not the pytest suite above.
