from the_village.discussion.speaker import DECLINED_TO_RESPOND
from the_village.state import WEEKDAYS, Day, DiscussionMessage, GameState, Player, VoteRecord


def test_player_defaults():
    player = Player(name="Alice", player_type="villager")
    assert player.is_pack_leader is False
    assert player.is_alive is True


def test_weekdays_starts_on_monday_ends_on_sunday():
    assert WEEKDAYS[0] == "Monday"
    assert WEEKDAYS[-1] == "Sunday"
    assert len(WEEKDAYS) == 7


def test_day_defaults():
    day = Day(day_number=1)
    assert day.player_found_dead is None
    assert day.discussion == []
    assert day.votes == []
    assert day.player_lynched is None


def test_discussion_message_fields():
    message = DiscussionMessage(player_name="Alice", text="hello")
    assert message.player_name == "Alice"
    assert message.text == "hello"
    assert message.addressed_to is None


def test_discussion_message_addressed_to():
    message = DiscussionMessage(
        player_name="Alice",
        text="Bram, where were you?",
        addressed_to="Bram",
    )
    assert message.addressed_to == "Bram"


def test_vote_record_defaults_to_abstain():
    vote = VoteRecord(voter_name="Alice")
    assert vote.target_name is None


def test_vote_record_with_target():
    vote = VoteRecord(voter_name="Alice", target_name="Bruce")
    assert vote.target_name == "Bruce"


def test_game_state_defaults():
    state = GameState()
    assert state.user_player_name == ""
    assert state.players == []
    assert state.days == [Day(day_number=1)]


def test_game_state_day_number_property_reads_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_found_dead="Bruce")])
    assert state.day_number == 2


def test_game_state_current_day_property_returns_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_found_dead="Bruce")])
    assert state.current_day == Day(day_number=2, player_found_dead="Bruce")


def test_advance_day_appends_a_new_day_and_returns_it():
    state = GameState()
    new_day = state.advance_day()

    assert new_day == Day(day_number=2)
    assert state.days == [Day(day_number=1), Day(day_number=2)]
    assert state.current_day == new_day
    assert state.day_number == 2


def test_living_player_names_excludes_the_dead():
    players = [
        Player(name="Alice", player_type="villager"),
        Player(name="Bram", player_type="villager", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.living_player_names() == ["Alice"]


def test_other_living_player_names_excludes_the_given_player_and_the_dead():
    players = [
        Player(name="Alice", player_type="villager"),
        Player(name="Bram", player_type="villager"),
        Player(name="Cass", player_type="villager", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.other_living_player_names("Alice") == ["Bram"]


def test_last_player_to_speak_returns_none_with_no_discussion():
    state = GameState()
    assert state.last_player_to_speak() is None


def test_last_player_to_speak_returns_the_last_speaker():
    state = GameState()
    state.current_day.discussion = [
        DiscussionMessage(player_name="Alice", text="hello"),
        DiscussionMessage(player_name="Bram", text="hi Alice"),
    ]

    assert state.last_player_to_speak() == "Bram"


def test_last_player_to_speak_returns_the_decliner_so_they_dont_speak_again_right_away():
    state = GameState()
    state.current_day.discussion = [
        DiscussionMessage(player_name="Alice", text="hello"),
        DiscussionMessage(player_name="Bram", text=DECLINED_TO_RESPOND),
    ]

    assert state.last_player_to_speak() == "Bram"


def test_format_current_day_names_the_weekday_for_the_current_day_number():
    state = GameState(user_player_name="Dana", days=[Day(day_number=2)])
    assert state.format_current_day() == "Today is Tuesday."


def test_format_deaths_with_no_deaths():
    state = GameState(user_player_name="Dana")
    assert state._format_deaths() == "(No one has been killed by the werewolves yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(
        user_player_name="Dana", days=[Day(day_number=2, player_found_dead="D")]
    )
    assert state._format_deaths() == "D was killed by the werewolves today."


def test_format_daily_history_with_no_messages():
    state = GameState(user_player_name="Dana")
    assert state._format_daily_history() == (
        "### Day 1: Monday\nNo one was killed today\n\n\n"
    )


def test_format_daily_history_includes_prior_days_in_order():
    state = GameState(
        user_player_name="Dana",
        days=[
            Day(
                day_number=1,
                discussion=[
                    DiscussionMessage(player_name="A", text="yesterday's message")
                ],
            ),
            Day(
                day_number=2,
                discussion=[DiscussionMessage(player_name="B", text="today's message")],
            ),
        ],
    )
    assert state._format_daily_history() == (
        "### Day 1: Monday\nNo one was killed today\n#### Discussion\n"
        "A: yesterday's message\n\n\n"
        "### Day 2: Tuesday\nNo one was killed today\n#### Discussion\n"
        "B: today's message\n\n"
    )


def test_format_daily_history_hides_votes_cast_so_far_on_the_current_day():
    state = GameState(
        user_player_name="Dana",
        days=[
            Day(
                day_number=1,
                votes=[VoteRecord(voter_name="A", target_name="B")],
            )
        ],
    )
    assert "Lynching Votes" not in state._format_daily_history()
    assert "A voted to lynch B" not in state._format_daily_history()


def test_format_daily_history_shows_votes_from_a_past_day():
    state = GameState(
        user_player_name="Dana",
        days=[
            Day(
                day_number=1,
                votes=[VoteRecord(voter_name="A", target_name="B")],
            ),
            Day(day_number=2),
        ],
    )
    history = state._format_daily_history()
    assert "Lynching Votes" in history
    assert "A voted to lynch B." in history


def test_game_state_winner_defaults_to_none():
    state = GameState()
    assert state.winner is None


def test_living_werewolves_count_excludes_the_dead():
    players = [
        Player(name="A", player_type="werewolf"),
        Player(name="B", player_type="werewolf", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.living_werewolves_count() == 1


def test_living_non_werewolves_count_includes_the_user():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.living_non_werewolves_count() == 2


def test_determine_winner_is_none_when_werewolves_are_outnumbered():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.determine_winner() is None


def test_determine_winner_is_werewolves_at_parity():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.determine_winner() == "werewolves"


def test_determine_winner_is_werewolves_when_a_mislynch_tips_the_balance():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),  # mislynched
        Player(name="B", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.determine_winner() == "werewolves"


def test_determine_winner_is_villagers_once_every_werewolf_is_dead():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="werewolf", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.determine_winner() == "villagers"


def test_determine_winner_is_villagers_with_no_werewolves_in_the_roster():
    state = GameState(players=[Player(name="Dana", player_type="user")])
    assert state.determine_winner() == "villagers"


def test_eligible_villagers_to_kill_excludes_werewolves_includes_user_and_villagers():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="W1", player_type="werewolf", is_pack_leader=True),
        Player(name="W2", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.eligible_villagers_to_kill() == ["Dana", "A", "B"]


def test_eligible_villagers_to_kill_excludes_the_dead():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager"),
        Player(name="W1", player_type="werewolf", is_pack_leader=True),
    ]
    state = GameState(players=players)
    assert state.eligible_villagers_to_kill() == ["Dana", "B"]


def test_werewolf_names_lists_all_werewolves_dead_or_alive():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="werewolf", is_alive=False),
        Player(name="B", player_type="werewolf"),
    ]
    state = GameState(players=players)
    assert state.werewolf_names() == ["A", "B"]
