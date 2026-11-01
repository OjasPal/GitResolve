"""Git tree publication, gated by exact human approval and trusted Docker evidence."""
import base64
import hashlib
from urllib.parse import quote

from githubapp.client import GitHubClientError
from githubapp.patch import apply_diff
from app.schemas.enums import FailureCode
from app.services.ids import sha256_hash
from app.services.github import GitHubUnavailable
from app.services.policy import validate_fix
from app.worker.pr import PullRequestError, PullRequestResult


class GitHubPullRequestCreator:
    def __init__(self, gateway):
        self.gateway = gateway

    def create(self, task, diff, *, checkpoint=lambda: None, event=lambda message: None):
        digest = sha256_hash(diff)
        approval = task.get('approval') or {}
        receipt = task.get('sandboxVerification') or {}
        if (approval.get('state') != 'approved' or approval.get('diffHash') != digest
                or task.get('finalDiffHash') != digest or receipt.get('source') != 'docker'
                or receipt.get('passed') is not True or receipt.get('diffHash') != digest
                or receipt.get('baseSha') != task.get('baseSha')
                or type(receipt.get('testsRun')) is not int or receipt['testsRun'] <= 0
                or receipt.get('command', [])[:3] != ['python', '-m', 'pytest']):
            raise PullRequestError(FailureCode.POLICY_VIOLATION,
                                   'Exact diff approval and passing Docker pytest execution are required.')
        try:
            validate_fix(diff, task['limits'], task.get('verification') or {}, task['baseSha'])
            checkpoint()
            repo, client = self.gateway.authorized_client(task['userId'], task['repoId'], write=True)
            if repo.full_name != task['repoFullName']:
                raise GitHubClientError('access_revoked')
            path = f'/repos/{repo.full_name}'
            branch = f"gitresolve/{task['id']}-{digest[-12:]}"
            owner = repo.full_name.split('/')[0]
            # Retry after a lost API response: find our existing PR before doing any writes.
            existing = client.request('GET', path + '/pulls', params={'head': f'{owner}:{branch}', 'state': 'all', 'per_page': 100})
            marker = f'<!-- gitresolve:{task["id"]}:{digest} -->'
            commit = client._get(path + '/git/commits/' + task['baseSha'])
            tree_sha = commit['tree']['sha']
            tree = client.request('GET', path + '/git/trees/' + tree_sha, params={'recursive': '1'})
            if tree.get('truncated') or len(tree['tree']) > 10000:
                raise GitHubClientError('repository_too_large')
            entries = {entry['path']: entry for entry in tree['tree']}
            def read_file(name):
                entry = entries.get(name)
                if not entry or entry['type'] != 'blob' or entry['mode'] not in ('100644', '100755') or entry.get('size', 0) > 256 * 1024:
                    raise GitHubClientError('unsupported_file')
                blob = client._get(path + '/git/blobs/' + entry['sha'])
                return base64.b64decode(blob['content'], validate=False).decode('utf-8'), entry['mode']
            files = apply_diff(diff, read_file)
            previous = None
            for line in diff.splitlines():
                if line.startswith('--- '):
                    previous = line[4:]
                if line.startswith('+++ b/') and previous == '/dev/null' and line[6:] in entries:
                    raise GitHubClientError('patch_conflict')
            # Reconcile a previous response loss only if its complete Git tree still
            # matches the approved patch. A marker alone is not evidence of contents.
            expected = {name: (entry['mode'], entry['sha']) for name, entry in entries.items()
                        if entry['type'] != 'tree'}
            for item in files:
                content = item['content'].encode('utf-8')
                blob_sha = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
                expected[item['path']] = (item['mode'], blob_sha)
            for pr in existing:
                if marker not in (pr.get('body') or '') or pr['base']['ref'] != task['defaultBranch']:
                    continue
                head = client._get(path + '/git/commits/' + pr['head']['sha'])
                actual = client.request('GET', path + '/git/trees/' + head['tree']['sha'], params={'recursive': '1'})
                actual_files = {entry['path']: (entry['mode'], entry['sha']) for entry in actual['tree']
                                if entry['type'] != 'tree'}
                if (actual.get('truncated') or actual_files != expected
                        or [parent['sha'] for parent in head['parents']] != [task['baseSha']]):
                    raise GitHubClientError('branch_conflict')
                return PullRequestResult(pr['number'], pr['html_url'], branch)
            checkpoint()
            new_tree = client.request('POST', path + '/git/trees', json={'base_tree': tree_sha, 'tree': files})
            checkpoint()
            new_commit = client.request('POST', path + '/git/commits', json={
                'message': f"Fix issue #{task['issue']['number']}\n\n{marker}",
                'tree': new_tree['sha'], 'parents': [task['baseSha']],
                # Stable identity/date make retries produce the same commit SHA.
                'author': {'name': 'GitResolve', 'email': 'gitresolve@users.noreply.github.com',
                           'date': task['createdAt'].isoformat()},
                'committer': {'name': 'GitResolve', 'email': 'gitresolve@users.noreply.github.com',
                              'date': task['createdAt'].isoformat()},
            })
            checkpoint()
            try:
                client.request('POST', path + '/git/refs', json={'ref': f'refs/heads/{branch}', 'sha': new_commit['sha']})
            except GitHubClientError:
                # Never force-update or overwrite an existing branch, even on a retry.
                ref = client._get(path + '/git/ref/heads/' + quote(branch, safe=''))
                if ref['object']['sha'] != new_commit['sha']:
                    raise GitHubClientError('branch_conflict') from None
            event('GitHub branch created for the approved diff.')
            checkpoint()
            pr = client.request('POST', path + '/pulls', json={
                'title': f"Fix #{task['issue']['number']}: {task['issue']['title']}"[:200],
                'head': branch, 'base': task['defaultBranch'],
                'body': f"Fixes #{task['issue']['number']}\n\nHuman-approved diff; Docker tests passed.\n\n{marker}",
                'draft': True,
            })
            return PullRequestResult(pr['number'], pr['html_url'], branch)
        except (GitHubClientError, GitHubUnavailable) as exc:
            code = getattr(exc, 'code', str(exc))
            failure = FailureCode(code) if code in {c.value for c in FailureCode} else FailureCode.GITHUB_ERROR
            raise PullRequestError(failure, 'GitHub operation failed: ' + failure.value) from None
        except (ValueError, KeyError, TypeError):
            raise PullRequestError(FailureCode.POLICY_VIOLATION, 'Repository or patch validation failed.') from None
