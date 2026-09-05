from abc import abstractmethod
import logging
import os
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tasks.llm_guardrail import LLMGuardrail
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge

from .speaker import _Speaker, DECLINED_TO_RESPOND
from the_village.state import DiscussionMessage, GameState, Player

logger = logging.getLogger(__name__)

_DEAD_AND_OUT_OF_GAME = "dead and out of the game"

# The guardrail judges speaker output against a multi-clause conditional
# prompt ("reject only if explicit", "don't infer"). The player's own model
# (MODEL, often a small/cheap one) proved unreliable at following those
# conditions faithfully -- it kept rejecting generic, ungrounded statements
# on invented implications despite repeated prompt tightening. Judging needs
# a stronger model than generating does, so the guardrail gets its own.
_GUARDRAIL_MODEL = os.environ.get("GUARDRAIL_MODEL", "gpt-5-mini")


class _SpeakerOutput(BaseModel):
    has_something_to_say: bool = Field(
        description=(
            "Whether you have something to say right now. False means you'll "
            "sit this turn out."
        )
    )
    text: str | None = Field(
        default=None,
        description=(
            "What you say, if you have something to say. Keep it to one or "
            "two sentences -- brief, like real spoken dialogue."
        ),
    )
    addressed_to: str | None = Field(
        default=None,
        description=(
            "The name of the living villager your text directly asks a "
            "question of, or explicitly demands a response from, if any. "
            "Merely accusing or talking about a player -- without asking "
            "them anything or demanding they respond -- doesn't count. "
            "Leave unset if the message isn't addressing anyone in "
            "particular, is addressed to the whole group or to multiple "
            "players, or if you have nothing to say."
        ),
    )


class _AiSpeaker(_Speaker):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
        player_agent: Agent,
        analyst_agent: Agent,
    ):
        super().__init__(state, bridge, player_name, analyst_agent)
        self._player_agent = player_agent

    async def _speak(
        self,
        addressed_by: DiscussionMessage | None,
    ) -> DiscussionMessage | None:

        speak_task = self._build_speak_task(addressed_by)
        crew = self._build_crew(speak_task)

        try:
            result = await crew.akickoff()
        except Exception as exc:
            if "guardrail" not in str(exc).lower():
                raise
            logger.warning(
                "%s's turn kept failing guardrail validation and was skipped: %s",
                self._player_name,
                exc,
            )
            if addressed_by is None:
                return None
            return self._record_message(DECLINED_TO_RESPOND, addressed_to=None)

        speakerOutput = result.tasks_output[0].pydantic or _SpeakerOutput(
            has_something_to_say=False
        )

        if not speakerOutput.has_something_to_say or not speakerOutput.text:
            if addressed_by is None:
                return None
            return self._record_message(DECLINED_TO_RESPOND, addressed_to=None)

        addressed_to = self._resolve_target(speakerOutput.addressed_to)
        return self._record_message(speakerOutput.text, addressed_to)

    def _build_speak_task(self, addressed_by: str | None) -> Task:
        return Task(
            description=self._build_speak_prompt(addressed_by),
            agent=self._player_agent,
            expected_output=(
                "A JSON object with `has_something_to_say` (bool); if true, "
                "`text`: one or two sentences of first-person spoken dialogue "
                "that stay consistent with everything you've said before and "
                "make no unfounded claims about other players; and "
                "`addressed_to`: the name of the single living player your "
                "text directly asks a question of or explicitly demands a "
                "response from, if any -- left unset when addressed to the "
                "whole group, addressed to multiple players, merely accuses "
                "or talks about someone without asking them anything, or "
                "when you have nothing to say."
            ),
            output_pydantic=_SpeakerOutput,
            guardrail=self._build_guardrail(),
            guardrail_max_retries=1,
        )

    def _build_guardrail(self):
        """Wraps CrewAI's own LLMGuardrail rather than passing a plain string,
        so each attempt's candidate text and rejection reason can be logged --
        the string-guardrail path CrewAI runs internally exposes neither
        through its events, which made a real production miss impossible to
        diagnose from logs alone."""
        llm_guardrail = LLMGuardrail(
            description=self._build_guardrail_description(),
            llm=LLM(model=_GUARDRAIL_MODEL),
        )
        attempt = 0

        def _guardrail(output: Any) -> tuple[bool, Any]:
            nonlocal attempt
            passed, result = llm_guardrail(output)
            if not passed:
                speaker_output: _SpeakerOutput | None = output.pydantic
                text = speaker_output.text if speaker_output else None
                logger.warning(
                    "%s's speaker output failed the LLM guardrail (attempt "
                    "%s): reason=%s text=%r",
                    self._player_name,
                    attempt,
                    result,
                    text,
                )
            attempt += 1
            return passed, result

        return _guardrail

    def _build_guardrail_description(self) -> str:
        parts = [
            self._state.known_facts(self._player_name),
            "",
            "The rules below reference the Known Facts and Daily History above "
            "-- use them to check whether a claim is actually grounded, rather "
            "than taking the speaker's wording at face value.",
            "",
            "The task result is a JSON object with a `text` field containing "
            "what the player said aloud. Judge only the content of that field "
            "against the rules below.",
            "If `has_something_to_say` is false, or the `text` field is "
            "null or empty, that is always valid -- there is nothing to "
            "judge.",
            "Be conservative: reject only if the text explicitly and "
            "directly violates a rule. Do not infer, assume, or read "
            "between the lines -- if a violation would require guessing at "
            "unstated intent, or the text is a generic question or request "
            "addressed to the whole group (e.g. asking what everyone did, "
            "or asking living players to share their whereabouts), it is "
            "valid. When you do reject, quote the exact phrase that "
            "violates the rule.",
            "Reject only if the text explicitly comments on turn order -- "
            "i.e. whether someone has spoken or been heard from at all "
            '(e.g. "you haven\'t said anything," "no one has spoken yet," '
            '"we haven\'t heard from you"). A statement that players '
            "haven't provided some specific piece of information (e.g. an "
            "alibi, an answer, evidence) is not turn-order commentary, "
            'even when phrased as "none of you have..." or "no one has '
            "provided...\" -- that's a request for content, not a comment "
            "on participation.",
            "Recapping a turn-order comment that a specific player "
            'actually made earlier in Discussion so far (e.g. "Stephen '
            "questioned Della's silence earlier\") is grounded reportage "
            "of what was said, not a fresh turn-order complaint, and is "
            "valid.",
            "Reject only if the text explicitly asserts, about a specific "
            "named player, that they have been acting oddly/strangely/"
            "suspiciously/nervously/shady/defensively/evasively, or "
            "displaying any other suspicious demeanor or reaction, or "
            "references their movements, without grounding it in "
            "something specific. This covers any wording with that same "
            "meaning, not just the examples listed. A generic question or "
            "request directed at the group is not a behavior claim about "
            "anyone.",
            "Reject only if the text explicitly claims a specific player "
            "voted for, or abstained from voting for, a specific lynch "
            "target, and that claim contradicts the actual vote recorded "
            'for them in Known Facts above (e.g. "you voted for Henry '
            'yesterday" when Known Facts records that player voting for '
            "Alice). A vague or unattributed reference to a past vote "
            '(e.g. "remember who you voted for") is not a claim that can '
            "be checked, and is valid.",
            "Reject only if the text states, as fact, a specific position, "
            "priority, or preference for a named player that directly "
            "contradicts what that player actually said earlier in "
            'Discussion so far (e.g. asserting a player "wants to mourn '
            'instead of investigating" or "would rather move on" when that '
            "player's own recorded words said the opposite). Quote or "
            "closely paraphrase the player's own prior statement to check "
            "this -- only reject when it's a direct contradiction, not "
            "merely an uncharitable reading. An opinion about a player's "
            "unstated motive, intent, or hidden agenda (e.g. accusing them "
            "of using a true statement as a distraction) is inference, not "
            "a claim about their recorded words, and is valid.",
            "Reject only if the text asks a named player whether they "
            "still hold, or asks them to reconsider or justify, a "
            "specific belief or suspicion that player has already "
            "explicitly and unambiguously abandoned earlier in Discussion "
            "so far (e.g. asking Don \"do you still think Bruce was the "
            "werewolf?\" right after Don said he was wrong to suspect "
            "Bruce). Quote or closely paraphrase the player's own prior "
            "statement to check this -- only reject when it already and "
            "directly settles the question being asked, not when the "
            "prior statement was hedged or ambiguous. A question about "
            "why they changed their mind, what they think now, or "
            "anything else not already settled by their own prior words, "
            "is valid.",
        ]
        dead_names = self._state.dead_players_names()
        if dead_names:
            names = ", ".join(dead_names)
            first_name = dead_names[0]
            verb = "is" if len(dead_names) == 1 else "are"
            parts.append(
                f"{names} {verb} {_DEAD_AND_OUT_OF_GAME}. The key test: "
                "forbid only text that treats them as someone who could "
                "still respond or act right now -- pressing them for new "
                "whereabouts, alibi, or explanations; claiming they are "
                "currently evading questions; comparing their ongoing "
                "credibility or story to a living player's; or suggesting "
                "they might still speak or be voted on. Generic statements "
                'addressed to "everyone" or the group do not count as '
                "naming them, since the living players obviously "
                "understand that to mean the living players."
            )
            parts.append(
                "Reject also if the text places one of them reacting, "
                "feeling, or behaving at a point in time after the day "
                "they died -- for example, describing their defensiveness "
                '"after the lynching," claiming they now "feel cornered," '
                'or saying they "just lost" someone who died after they '
                "themselves did. A dead player's demeanor or reaction can "
                "only be described as something from before they died."
            )
            parts.append(
                "Everything else about a dead player is valid: discussing "
                "why or how they died; asking a living player about their "
                "own whereabouts or actions, even when the dead player's "
                "name or death is used only as a time reference (e.g. "
                f'"where were you when {first_name} was killed", "do you '
                f'have an alibi for when {first_name} died"); a flat '
                "factual or emotional statement about them on its own "
                f'(e.g. "{first_name} was killed", "this is terrible news '
                f'about {first_name}"); and analyzing their own past '
                "actions, statements, or apparent beliefs from while they "
                "were alive -- including what they seemed to think about "
                "another player, living or dead -- as investigative "
                f'reasoning about what already happened (e.g. "Don was '
                'quick to accuse Martha -- maybe he was deflecting", "Don '
                'seemed to think Martha was acting suspicious"), as long as '
                "it's grounded in Discussion so far or Known facts."
            )
        if dead_names and not self._state.is_werewolf(self._player_name):
            parts.append(
                "Reject only if the text explicitly claims the speaker personally "
                "knew, heard, suspected, or otherwise learned that a specific "
                "killing had happened before it was found and announced in Known "
                'Facts above (e.g. claiming to have heard the news "last night," '
                "or before the morning the body was discovered). A statement that "
                "only refers to a death after it was found, or a generic reaction "
                "to the news without any specific pre-discovery timing claim, is "
                "valid."
            )
        return "\n\n".join(parts)

    def _build_speak_prompt(self, addressed_by: DiscussionMessage | None) -> str:
        parts = [
            self._state.known_facts(self._player_name),
            "",
            "# Instructions",
            self._prompt_instructions(),
        ]

        if addressed_by is not None:
            parts.append(
                f'{addressed_by.player_name} just said to you: "{addressed_by.text}" '
                "Respond directly to this. You can opt to say nothing if it is in your "
                "best interest.",
            )
        else:
            parts.append(
                "It's your turn. Decide whether you have something to say -- a "
                "statement, question, or accusation. If you have nothing to add, "
                "say so."
            )
        return "\n".join(parts)

    def _prompt_instructions(self) -> str:
        parts = [
            f"Today's discussion should focus on {self._state.current_day.player_found_dead}'s "
            "killing, since that's what just happened -- but don't ignore any earlier "
            "discussions, killings, lynchings, or votes listed in Known Facts above; bring them up when "
            "they're relevant.",
            "",
            "## Rules",
            "",
            "1. Only treat something as true if it's listed in Known Facts above or was "
            "actually said in this discussion or previous discussions.",
            "",
            "2. You may make things up about yourself -- for example, inventing an alibi. You must "
            "remain consistent throughout all of the discussions.  Do NOT say contradictory things.",
            "",
            "3. Anything you say about someone else must be grounded in what you actually know or "
            "what has already been said.  Do NOT make any claims about what another villager did, "
            "or said.",
            "",
            f"4. {self._inner_prompt_instructions().strip()}",
            "",
            "5. You speak the way people actually do in a tense group conversation: briefly. "
            "One or two sentences, never a speech. Speak in first person as yourself -- "
            "never refer to yourself by name or in the third person.",
            "",
            f"6. Players listed above as killed or lynched are {_DEAD_AND_OUT_OF_GAME} -- "
            "never treat them as an active suspect (pressing them for "
            "answers, comparing their story to a living player's, accusing "
            "them, and so on), and never describe their reactions, "
            "feelings, or behavior as happening after the day they died -- "
            "for example, a dead player can't be \"defensive after the "
            "lynching\" or \"feel cornered\" over something that happened "
            "after their own death. It's still fine to discuss why or how a "
            "dead player died, and to ask living players about their own "
            "whereabouts or actions.",
            "",
            "7. When referring to another player, always use their name -- never a pronoun.",
            "",
            "8. Any statements, questions, or accusations must be consistent with what you previously said.",
            "",
            "9. Before asking a player whether they still hold a belief or suspicion, or "
            "asking them to reconsider or justify one, check whether their own words in "
            "Discussion so far already answer that. If they do, don't ask it again -- "
            "respond to what they actually said instead (agree, push back on it, or move "
            "on to a different angle).",
            "",
            "10. Check the \"Heavily Discussed Today\" list in Known Facts above. If a "
            "player listed there keeps coming up without new information, don't just "
            "restate a question or accusation about them -- either add something "
            "genuinely new, or shift focus to a different player or angle.",
            "",
            "11. When someone new turns up dead, check their own voting history in the "
            "Daily History in Known Facts above. If they cast a lone or minority vote "
            "for someone who's still alive, that's worth raising as a possible reason "
            "the werewolves targeted them.",
            "",
        ]
        if not self._state.is_werewolf(self._player_name):
            parts.append(
                "12. You only learn that someone was killed when their body is found the "
                "next morning, as recorded in Known Facts above -- you have no knowledge "
                "of a death before it's discovered. Never claim to have heard, suspected, "
                "or known about a killing before it was found (for example, hearing the "
                'news "last night," or before the morning it was announced).'
            )
            parts.append("")
            parts.append(
                "13. When inventing your own alibi, never base it on what another player "
                "already claimed (for example, saying you were with someone just because "
                "they already claimed it). Your alibi must be your own invention, not a "
                "copy of someone else's."
            )
            parts.append("")
        return "\n".join(parts)

    @abstractmethod
    def _inner_prompt_instructions(self) -> str:
        pass

    def _build_crew(self, speak_task: Task) -> Crew:
        return Crew(
            agents=[self._player_agent],
            tasks=[speak_task],
            process=Process.sequential,
        )
