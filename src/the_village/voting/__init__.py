from .voting import Voting, VoteOutcome, tally_votes

# Explicitly define ONLY the public functions allowed outside the folder
__all__ = ["Voting", "VoteOutcome", "tally_votes"]
