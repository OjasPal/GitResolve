from app import app
def test_route(): assert app.test_client().get("/").data == b"hello"
