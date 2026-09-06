from the_village.roster.personalities import PERSONALITIES


def test_has_five_distinct_personalities():
    assert len(PERSONALITIES) == 5


def test_every_personality_has_a_nonempty_description():
    assert all(isinstance(text, str) and text.strip() for text in PERSONALITIES.values())


def test_personality_descriptions_are_unique():
    descriptions = list(PERSONALITIES.values())
    assert len(descriptions) == len(set(descriptions))
