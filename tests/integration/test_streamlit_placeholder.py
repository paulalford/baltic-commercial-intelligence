"""Exercise the Streamlit placeholder without starting a server."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_landing_page_discloses_synthetic_status_and_has_no_runtime_exception() -> None:
    app_path = Path(__file__).parents[2] / "app" / "Home.py"
    app = AppTest.from_file(str(app_path)).run(timeout=10)

    assert not app.exception
    visible_text = " ".join(
        [element.value for element in [*app.title, *app.subheader, *app.info, *app.markdown]]
    )
    assert "Fictional synthetic commercial analytics case study" in visible_text
    assert "No business data" in visible_text
