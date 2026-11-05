from pathlib import Path

from sandbox.runner import _docker_run_args, _clean_output


def test_workspace_is_bounded_and_input_read_only():
    args = _docker_run_args("test", Path("/fixture"), ["python", "-m", "pytest"])
    assert f"type=bind,source={Path('/fixture')},target=/input,readonly" in args
    assert "/workspace:rw,nosuid,nodev,size=1g,uid=10001,gid=10001" in args
    assert args[args.index("--network") + 1] == "none"
    assert "--read-only" in args
    assert args[args.index("--user") + 1] == "10001:10001"


def test_utf8_output_never_exceeds_byte_limit():
    text, truncated = _clean_output("€€", 4)
    assert truncated and len(text.encode()) <= 4
