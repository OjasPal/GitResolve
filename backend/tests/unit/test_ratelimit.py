from app.core.ratelimit import SlidingWindowLimiter, client_ip


def test_limiter_window_retry_after_and_independent_keys():
    now = [0.0]
    limiter = SlidingWindowLimiter(2, 60, clock=lambda: now[0])
    assert limiter.hit("a") is None and limiter.hit("a") is None
    retry = limiter.hit("a")
    assert retry is not None and 1 <= retry <= 60
    assert limiter.hit("b") is None
    now[0] = 61
    assert limiter.hit("a") is None


def test_limit_zero_disables():
    limiter = SlidingWindowLimiter(0)
    assert all(limiter.hit("a") is None for _ in range(1000))


def scope(xff: str | None, host: str = "9.9.9.9"):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return {"headers": headers, "client": (host, 1234)}


def test_client_ip_ignores_forwarded_header_without_trusted_proxy():
    assert client_ip(scope("1.1.1.1"), 0) == "9.9.9.9"


def test_client_ip_takes_entry_from_the_right_so_spoofing_does_not_help():
    # A client may prepend anything; the trusted proxy appends the real address last.
    assert client_ip(scope("6.6.6.6, 203.0.113.7"), 1) == "203.0.113.7"
    assert client_ip(scope("a, b, 203.0.113.7, 10.0.0.1"), 2) == "203.0.113.7"


def test_client_ip_falls_back_when_header_is_missing_or_too_short():
    assert client_ip(scope(None), 1) == "9.9.9.9"
    assert client_ip(scope("1.1.1.1"), 2) == "9.9.9.9"
    assert client_ip({"headers": [], "client": None}, 1) == "unknown"
