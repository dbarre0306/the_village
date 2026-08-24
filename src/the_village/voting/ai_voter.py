from crewai import Agent, Crew, Task
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge
from the_village.state import GameState, _weekday

from .voter import _Voter


class _VoteChoice(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "The name of the living villager you vote to lynch, or leave "
            "unset to abstain."
        ),
    )


def _format_lynchings(state: GameState) -> str:
    lynched_days = [day for day in state.days if day.player_lynched]
    if not lynched_days:
        return "(No one has been lynched yet.)"
    return "\n".join(
        f"{day.player_lynched} was lynched by the village on {_weekday(day.day_number)}."
        for day in lynched_days
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
        return self._resolve_target(choice.target)

    def _build_vote_task(self) -> Task:
        return Task(
            description=self._build_vote_prompt(),
            agent=self._player_agent,
            expected_output="A VoteChoice naming who, if anyone, to lynch.",
            output_pydantic=_VoteChoice,
        )

    def _build_vote_prompt(self) -> str:
        candidates = self._state.names_of_other_living_players(self._player_name)
        return "\n".join(
            [
                "Known facts:",
                self._state.format_deaths(),
                _format_lynchings(self._state),
                "",
                f"Living villagers you may vote to lynch: {', '.join(candidates)}.",
                "",
                "Discussion so far:",
                self._state.format_history(),
                "",
                "It's time to vote. Decide who you believe is responsible for "
                "the killing and vote to lynch them, or leave your vote unset "
                "to abstain. You may not vote for yourself.",
            ]
        )
