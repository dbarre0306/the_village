import gradio as gr

from the_village.main import VillageFlow
from the_village.state import WEEKDAYS, GameState


def format_event_log(state: GameState) -> str:
    if not state.deaths:
        return "Nothing has happened yet."
    lines = [
        f"{WEEKDAYS[(death.day_number - 1) % 7]} morning: {death.name} was found dead."
        for death in state.deaths
    ]
    return "\n\n".join(lines)


def format_deaths_panel(state: GameState) -> str:
    if not state.deaths:
        return "No one has been killed yet."
    lines = [
        f"{WEEKDAYS[(death.day_number - 1) % 7]}: {death.name}"
        for death in state.deaths
    ]
    return "\n".join(lines)


def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    try:
        flow = VillageFlow()
        flow.kickoff(inputs={"player_name": player_name.strip()})
    except gr.Error:
        raise
    except Exception:
        raise gr.Error("Something went wrong, please try again.")

    state = flow.state
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="The Village") as demo:
        with gr.Column(visible=True) as start_screen:
            name_input = gr.Textbox(label="Your first name")
            start_button = gr.Button("Start Game")

        with gr.Row(visible=False) as result_screen:
            with gr.Column():
                gr.Markdown("### Events")
                event_log = gr.Markdown()
            with gr.Column():
                gr.Markdown("### Killed by Werewolves")
                deaths_panel = gr.Markdown()

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[start_screen, result_screen, event_log, deaths_panel],
        )

    return demo


def main():
    build_app().launch()


if __name__ == "__main__":
    main()
