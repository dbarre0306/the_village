from the_village.ui import (
    _autofocus_js,
    _autoscroll_js,
    _chronicle_css,
    _game_over_scroll_js,
    _layout_css,
    _speaker_color_css,
    build_app,
)


def main():
    build_app().launch(
        css=_speaker_color_css() + _layout_css() + _chronicle_css(),
        js=_autoscroll_js() + _autofocus_js() + _game_over_scroll_js(),
    )


if __name__ == "__main__":
    main()
