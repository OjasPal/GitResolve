"""Strict text patch application for Git tree blobs; never executes repository code."""
import re
from githubapp.client import GitHubClientError


def apply_diff(diff, read_file, allowed_deletions=()):
    if allowed_deletions and (not 1 <= len(allowed_deletions) <= 4 or any(not re.fullmatch(r"test[A-Za-z0-9_]*\.py",n) for n in allowed_deletions)):
        raise GitHubClientError("unsupported_file")
    lines = diff.splitlines(keepends=True)
    files = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith('--- '):
            i += 1
            continue
        old = lines[i][4:].rstrip('\r\n')
        i += 1
        if i >= len(lines) or not lines[i].startswith(('+++ b/','+++ /dev/null')):
            raise GitHubClientError('invalid_diff')
        deleting = lines[i].rstrip('\r\n') == '+++ /dev/null'
        name = old[2:] if deleting and old.startswith('a/') else lines[i][6:].rstrip('\r\n')
        if deleting and name not in allowed_deletions: raise GitHubClientError('invalid_diff')
        if not name or any(p in ('', '.', '..') for p in name.split('/')) or '\\' in name:
            raise GitHubClientError('invalid_diff')
        original, mode = ('', '100644') if old == '/dev/null' else read_file(name)
        source = original.splitlines(keepends=True)
        output, cursor, hunks = [], 0, 0
        i += 1
        while i < len(lines) and not lines[i].startswith(('diff --git ', '--- ')):
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\r\n]*\r?\n?', lines[i])
            if not match:
                raise GitHubClientError('invalid_diff')
            start, count, new_start, new_count = (int(match[1]), int(match[2] or '1'),
                                                int(match[3]), int(match[4] or '1'))
            index = start - 1 if count else start
            if index < cursor or index > len(source):
                raise GitHubClientError('invalid_diff')
            output.extend(source[cursor:index]); cursor = index
            if len(output) != (new_start - 1 if new_count else new_start):
                raise GitHubClientError('invalid_diff')
            removed = added = 0
            i += 1; hunks += 1
            while i < len(lines) and lines[i][:1] in (' ', '+', '-') and not (removed == count and added == new_count):
                line = lines[i]; content = line[1:]; i += 1
                if i < len(lines) and lines[i].startswith('\\ No newline at end of file'):
                    content = content.removesuffix('\n'); i += 1
                if line[0] in (' ', '-'):
                    if cursor >= len(source) or source[cursor] != content:
                        raise GitHubClientError('patch_conflict')
                    cursor += 1; removed += 1
                if line[0] in (' ', '+'):
                    output.append(content); added += 1
            if removed != count or added != new_count:
                raise GitHubClientError('invalid_diff')
        if not hunks:
            raise GitHubClientError('invalid_diff')
        output.extend(source[cursor:])
        content = ''.join(output)
        if len(content.encode()) > 256 * 1024 or mode not in ('100644', '100755'):
            raise GitHubClientError('unsupported_file')
        if deleting:
            if content or cursor != len(source) or mode != '100644': raise GitHubClientError('invalid_diff')
            files.append({'path':name,'mode':mode,'type':'blob','sha':None})
        else:
            files.append({'path': name, 'mode': mode, 'type': 'blob', 'content': content})
    if not files:
        raise GitHubClientError('invalid_diff')
    return files
