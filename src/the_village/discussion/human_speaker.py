from crewai import Agent, Crew, Task

from the_village.bridge import FlowStatus, SessionBridge
from the_village.state import DiscussionMessage, GameState, Player

from .speaker import DECLINED_TO_RESPOND, _AddressResolution, _Speaker


class _HumanSpeaker(_Speaker):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
        analyst_agent: Agent,
    ):
        super().__init__(state, bridge, player_name, analyst_agent)

    async def _speak(
        self,
        addressed_by: DiscussionMessage | None,
    ) -> DiscussionMessage | None:
        status = (
            FlowStatus.WAITING_FOR_ANSWER
            if addressed_by
            else FlowStatus.WAITING_FOR_TURN
        )
        await self._bridge.outbox.put(status)
        player_input = await self._bridge.wait_for_input()

        if player_input.text is None:
            if addressed_by is None:
                return None
            return self._record_message(DECLINED_TO_RESPOND, addressed_to=None)

        addressed_to = await self._resolve_address(player_input.text)
        return self._record_message(player_input.text, addressed_to)

    async def _resolve_address(self, text: str) -> str | None:
        task = self._build_analyze_task(text)
        crew = Crew(agents=[self._analyst_agent], tasks=[task])

        result = await crew.akickoff()
        resolution = result.tasks_output[0].pydantic or _AddressResolution(
            addressed_to=None
        )
        return self._resolve_target(resolution.addressed_to)

    def _build_analyze_task(self, text: str) -> Task:
        return Task(
            description=self._build_analyze_prompt(text),
            agent=self._analyst_agent,
            expected_output="An AddressResolution naming who, if anyone, was addressed.",
            output_pydantic=_AddressResolution,
        )

    def _build_analyze_prompt(self, text: str) -> str:
        return "\n".join(
            [
                f"Names of other players: {', '.join(self._names_of_other_living_players)}.",
                "",
                f'{self._player_name} just said: "{text}"',
                "Who, if anyone, is this message directed at? A name that's "
                "merely mentioned doesn't count -- only someone actually being "
                "spoken to.",
            ]
        )
