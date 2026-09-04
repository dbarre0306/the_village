import logging

from the_village.voting.ai_voter import _AiVoter

logger = logging.getLogger(__name__)


class _VillagerVoter(_AiVoter):

    def _inner_prompt_instructions(self) -> str:
        parts = [
            "Decide who you believe is responsible for the killing based on "
            "what was actually said in the discussion -- who seemed evasive, "
            "inconsistent, or too eager to point fingers at others. You may "
            "consider previous discussions as well as how players have voted in "
            "the past. Vote to lynch the player you think is a werewolf, or "
            "leave your vote unset to abstain if no one "
            "stands out. Don't jump to conclusions without something in the "
            "discussion to back it up.",
        ]
        return "\n".join(parts)
