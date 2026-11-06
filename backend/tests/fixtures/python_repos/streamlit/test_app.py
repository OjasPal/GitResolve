from streamlit.testing.v1 import AppTest
def test_app():
    app = AppTest.from_file("app.py").run()
    assert not app.exception
    assert app.title[0].value == "Fixture"
