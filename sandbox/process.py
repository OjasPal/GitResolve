"""Bounded subprocess capture; no host shell and no unbounded pipe buffers."""
import subprocess
import threading
import time
from dataclasses import dataclass


class SandboxError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def checkpoint(deadline, cancel):
    if time.monotonic() >= deadline:
        raise SandboxError("command_timeout", "Execution deadline exceeded.")
    if cancel and cancel():
        raise SandboxError("cancelled", "Execution cancelled.")


@dataclass
class Captured:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool


def capture(args, deadline, cancel=None, limit=1_000_000, env=None):
    checkpoint(deadline, cancel)
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as proc:
        buffers = [bytearray(), bytearray()]
        truncated = [False, False]
        lock = threading.Lock()
        def drain(stream, index):
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                with lock:
                    available = max(0, limit - sum(map(len, buffers)))
                    buffers[index].extend(chunk[:available])
                    truncated[index] |= len(chunk) > available
        threads = [threading.Thread(target=drain, args=(stream, i), daemon=True)
                   for i, stream in enumerate((proc.stdout, proc.stderr))]
        for thread in threads:
            thread.start()
        try:
            while proc.poll() is None:
                checkpoint(deadline, cancel)
                time.sleep(0.05)
            checkpoint(deadline, cancel)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
        return Captured(proc.returncode, bytes(buffers[0]), bytes(buffers[1]), *truncated)
