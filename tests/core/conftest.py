from types import SimpleNamespace

from the_village.discussion.ai_speaker import _SpeakerOutput
from the_village.discussion.speaker import _AddressResolution
from the_village.pick_victim.werewolf_pack import _VictimChoice
from the_village.voting.ai_voter import _VoteChoice


async def _decline_and_abstain_akickoff(crew):
    """Every AI villager declines to speak during discussion and abstains
    when voting -- a deterministic stand-in for real kickoff() calls so
    tests can drive the whole flow to completion without hitting an LLM.
    Dispatches on each task's output_pydantic, since an AI speaker's
    discussion-turn crew has one task (_SpeakerOutput), the human player's
    message-analysis crew also has one (_AddressResolution), a vote's crew
    has one (_VoteChoice), and a kill_next_victim crew has one or more
    tasks with only the last carrying _VictimChoice.

    Shared by tests/core/test_flow.py and tests/core/test_concurrency.py --
    lives here (rather than being imported test-module-to-test-module) since
    tests/ has no __init__.py, so `tests.core.test_flow` isn't importable as
    a package from the repo root.
    """
    outputs = []
    for task in crew.tasks:
        if task.output_pydantic is _SpeakerOutput:
            outputs.append(_SpeakerOutput(has_something_to_say=False))
        elif task.output_pydantic is _AddressResolution:
            outputs.append(_AddressResolution(addressed_to=None))
        elif task.output_pydantic is _VoteChoice:
            outputs.append(_VoteChoice(target=None))
        elif task.output_pydantic is _VictimChoice:
            outputs.append(_VictimChoice(target=None))
    return SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=o) for o in outputs])
