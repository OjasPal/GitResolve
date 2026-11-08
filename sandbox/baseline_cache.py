"""Only successful original-suite results are reusable; never patched verification."""
import hashlib
import json
import re
from githubapp.cache import TTLCache

CACHE = TTLCache(ttl=300, capacity=32)


def key_for(request, scope, environment_hash, image_digest):
    if not scope or request.patch or request.allowed_deletions:
        return None
    if request.command[:3] != ['python', '-m', 'pytest']:
        return None
    if any(a in {'--collect-only', '--co', '--help', '-h', '--version'} for a in request.command):
        return None
    fields = request.model_dump(exclude={'execution_id', 'timeout_seconds'})
    fields.update(scope=scope, environment_hash=environment_hash,
                  image_digest=image_digest, verifier='baseline-v1')
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def remember(key, result):
    if (key and result.status == 'passed' and result.exit_code == 0 and result.cleanup_ok
            and not result.oom_killed and not result.timed_out
            and not result.stdout_truncated and not result.stderr_truncated
            and re.search(r'\b[1-9][0-9]* passed\b', result.stdout)):
        CACHE.put(key, result)
