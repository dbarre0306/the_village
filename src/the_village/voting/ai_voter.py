from abc import abstractmethod
import logging

from crewai import Agent, Crew, Task
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge
from the_village.state import GameState

from .voter import _Voter

logger = logging.getLogger(__name__)


class _VoteChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "The name of the living villager you vote to lynch, or leave "
            "unset to abstain."
        ),
    )


class _AiVoter(_Voter):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
        player_agent: Agent,
    ):
        super().__init__(state, bridge, player_name)
        self._player_agent = player_agent

    async def _cast(self) -> str | None:
        task = self._build_vote_task()
        crew = Crew(agents=[self._player_agent], tasks=[task])
        result = await crew.akickoff()
        choice = result.tasks_output[0].pydantic or _VoteChoice()
        target = self._resolve_target(choice.target)
        if choice.target and target is None:
            logger.warning(
                "Discarding %s's invalid vote for %r", self._player_name, choice.target
            )
        return target

    def _build_vote_task(self) -> Task:
        return Task(
            description=self._build_vote_prompt(),
            agent=self._player_agent,
            expected_output="A VoteChoice naming who, if anyone, to lynch.",
            output_pydantic=_VoteChoice,
        )

    def _build_vote_prompt(self) -> str:
        parts = [
            self._state.known_facts(self._player_name),
            "",
            "# Instructions",
            "It's time to vote. You may not vote for yourself. If you vote for "
            "someone, that player must be a living player.",
            "",
            self._inner_prompt_instructions(),
        ]
        return "\n".join(parts)

    @abstractmethod
    def _inner_prompt_instructions(self) -> str:
        pass
