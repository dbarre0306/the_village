from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum

from crewai import Agent

from the_village.state import Winner

logger = logging.getLogger(__name__)


class FlowStatus(str, Enum):
    WAITING_FOR_TURN = "waiting_for_turn"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    WAITING_FOR_VOTE = "waiting_for_vote"
    DISCUSSION_COMPLETE = "discussion_complete"
    VOTING_COMPLETE = "voting_complete"


@dataclass
class PlayerInput:
    text: str | None = None  # None means "passed"


@dataclass
class GameOverResult:
    winner: Winner
    werewolf_names: list[str]


@dataclass
class SessionBridge:
    """The sole channel between a session's background Flow task and Gradio.

    `outbox` carries Flow -> UI updates (DiscussionMessage | VoteOutcome |
    GameOverResult | str | FlowStatus -- the death announcement is a bare
    str, the victim's name; VoteOutcome is the vote reveal, put on once
    everyone has voted; GameOverResult is the last item ever put on a
    given session's outbox, once a winner is decided);
    `pending_input` carries the one UI -> Flow value a paused Flow step is
    waiting on. Reused for every pause point across the whole session (the
    death-announcement gate, every discussion turn) rather than built fresh
    per pause, so ui.py has one bridge per session to hold in `gr.State`.
    """

    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    pending_input: asyncio.Future[PlayerInput] | None = None
    task: asyncio.Task | None = None
    player_agents: dict[str, Agent] = field(default_factory=dict)
    # How many discussion messages ui.py has actually paced onto screen so
    # far, across the whole session. Lives here (not derived from
    # GameState.days at read time) because the background Flow task appends
    # to the live GameState -- and can race ahead of the UI's pacing --
    # without waiting for the UI to consume the corresponding outbox item.
    revealed_discussion_messages: int = 0
    # ui.py's discussion_status.change() -> start_voting binding fires again
    # when start_voting itself later rewrites discussion_status (swapping
    # the ballot question for "The Village Votes" label once the outcome is
    # known) -- that second, spurious invocation must not call
    # resolve_input(): by the time it runs, a *later* pause point (the next
    # day's announce_death) may already be the one pending, and resolving it
    # with an empty PlayerInput() would silently skip that day's Begin gate.
    # ui.py sets this True on a round's first (real) start_voting call and
    # resets it False when begin_discussion starts the next round.
    voting_started: bool = False

    async def wait_for_input(self) -> PlayerInput:
        self.pending_input = asyncio.get_event_loop().create_future()
        try:
            return await self.pending_input
        finally:
            self.pending_input = None

    def resolve_input(self, player_input: PlayerInput) -> bool:
        """Unblock a paused `wait_for_input()`. False if nothing is pending
        (or it was already resolved) -- callers treat that as a no-op,
        the session-scoped replacement for Gradio's removed concurrency_id
        double-click guard."""
        if self.pending_input is None or self.pending_input.done():
            return False
        self.pending_input.set_result(player_input)
        return True


@dataclass
class FlowFailed:
    detail: str = "Something went wrong."


async def run_flow(coro, bridge: SessionBridge) -> None:
    """Runs a background Flow coroutine, turning an exception into a
    FlowFailed on the outbox instead of an unawaited-task traceback that
    never reaches the player. Wrap VillageFlow.kickoff_async(...) in this
    whenever it's launched as a background asyncio.Task."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: anything
        # from here must reach the player via the outbox, not vanish.
        logger.exception("Background flow failed")
        await bridge.outbox.put(FlowFailed(detail=str(exc)))
