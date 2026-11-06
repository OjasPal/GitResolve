"""Aggregate timing only for the explicitly selected owner's historical issue run."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.core.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--github-login', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--issue', type=int, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    settings = Settings()
    docs = json.loads(Path(settings.local_state_path).read_text(encoding='utf-8'))
    ids = [k.split('/')[1] for k, v in docs.items() if k.startswith('users/') and k.count('/') == 1
           and v.get('githubLogin', '').casefold() == args.github_login.casefold()]
    if len(ids) != 1:
        raise SystemExit('Requested local identity is not unique.')
    tasks = [(k, v) for k, v in docs.items() if k.startswith('tasks/') and k.count('/') == 1
             and v.get('userId') == ids[0] and v.get('repoFullName', '').casefold() == args.repository.casefold()
             and v.get('issue', {}).get('number') == args.issue]
    report = {'matching_runs': len(tasks), 'runs': []}
    for key, task in tasks:
        events = sorted([v for k, v in docs.items() if k.startswith(key + '/events/')], key=lambda e: e.get('seq', 0))
        stages = {}
        for left, right in zip(events, events[1:]):
            try:
                duration = (datetime.fromisoformat(right['ts']) - datetime.fromisoformat(left['ts'])).total_seconds()
                stage = left.get('stage', 'unknown')
                stages[stage] = stages.get(stage, 0) + max(0, duration)
            except (ValueError, TypeError, KeyError):
                continue
        report['runs'].append({'status': task.get('status'), 'event_interval_seconds_by_stage': stages,
            'usage': {key: task.get('budgets', {}).get(key) for key in ('modelCalls', 'inputTokens', 'outputTokens', 'execSeconds')},
            'note': 'Event intervals include waits; historical per-stage token and Docker counts unavailable.'})
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
