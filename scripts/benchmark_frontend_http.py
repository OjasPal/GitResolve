"""Public route HTTP timings, deliberately not browser navigation/hydration claims."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.core.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    bases = [('production', 'http://localhost:3103'), ('development', 'http://localhost:3000')]
    configured = Settings().frontend_base_url.rstrip('/')
    if urlsplit(configured).scheme == 'https' and not urlsplit(configured).username:
        bases.append(('configured_tunnel', configured))
    rows = []
    for mode, base in bases:
        for iteration in (1, 2):
            for route in ('dashboard', 'settings', 'tasks', 'tasks/new'):
                start = time.perf_counter()
                try:
                    response = requests.get(f'{base}/{route}', timeout=15, allow_redirects=False)
                    status = response.status_code
                except requests.RequestException:
                    status = 'unavailable'
                row = {'mode': mode, 'route': route, 'pass': iteration, 'status': status,
                       'http_ms': round((time.perf_counter()-start)*1000, 3)}
                rows.append(row)
                Path(args.output).write_text(json.dumps(rows, indent=2))
                print(mode, route, iteration, status, row['http_ms'], flush=True)
    shared = Path(__file__).resolve().parents[1] / '.diagnostics/shared-backend.json'
    if shared.is_file():
        tunnel = json.loads(shared.read_text(encoding='utf-8')).get('url', '')
        parsed = urlsplit(tunnel)
        if (parsed.scheme == 'https' and parsed.hostname and parsed.hostname.endswith('.trycloudflare.com')
                and not parsed.username and not parsed.query):
            for mode, base in [('backend_local_8000', 'http://localhost:8000'),
                               ('backend_local_8010', 'http://localhost:8010'), ('backend_tunnel', tunnel.rstrip('/'))]:
                for iteration in (1, 2):
                    start = time.perf_counter()
                    try:
                        status = requests.get(base + '/health', timeout=10, allow_redirects=False).status_code
                    except requests.RequestException:
                        status = 'unavailable'
                    row = {'mode': mode, 'route': 'health', 'pass': iteration, 'status': status,
                           'http_ms': round((time.perf_counter()-start)*1000, 3)}
                    rows.append(row)
                    Path(args.output).write_text(json.dumps(rows, indent=2))
                    print(mode, iteration, status, row['http_ms'], flush=True)


if __name__ == '__main__': main()
