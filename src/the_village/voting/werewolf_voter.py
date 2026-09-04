import logging

from the_village.voting.ai_voter import _AiVoter

logger = logging.getLogger(__name__)


class _WerewolfVoter(_AiVoter):

    def _inner_prompt_instructions(self) -> str:
        other_werewolves = [
            name
            for name in self._state.living_werewolf_names()
            if name != self._player_name
        ]
        parts = []
        if other_werewolves:
            parts.append(
                f"The other living werewolf(ves): {', '.join(other_werewolves)}. "
                "You know this, no one else does."
            )
        parts.append(
            "Never vote for yourself or a fellow werewolf. Vote for a villager "
            "whose lynching would help take suspicion off you and your pack "
            "-- ideally whoever you or the other players have already been "
            "steering blame toward in the discussion, so your vote stays "
            "consistent with what you argued there. If no such target exists, "
            "pick the villager you believe is mostly likely going to be picked "
            "by the other players.  One last thing to consider: the rule to never "
            "vote for another werewolf is not absolute.  If it appears that the "
            "villagers are targetting another werewolf, you can also vote for that "
            "werewolf to avoid suspicion.  This is self preservation."
        )
        return "\n".join(parts)
