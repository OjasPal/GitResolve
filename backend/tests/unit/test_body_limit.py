import asyncio

from app.core.body_limit import RequestBodyLimitMiddleware


def test_body_limit_rejects_chunked_body_without_content_length():
    pending = [
        {"type": "http.request", "body": b"x" * 64, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": False},
    ]
    sent = []

    async def receive():
        return pending.pop(0)

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        raise AssertionError("Oversized requests must not reach the application.")

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=64)
    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "headers": []},
            receive,
            send,
        )
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert b"payload_too_large" in sent[1]["body"]
