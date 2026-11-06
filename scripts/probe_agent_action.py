"""One bounded real schema probe through ModelRouter; no repository/user contents."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Annotated, Literal
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.router import ModelRouter
from agent.config import get_router_settings
from githubapp.timing import capture_timings


class ReadAction(BaseModel):
    kind: Literal['read_file']
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class FinishAction(BaseModel):
    kind: Literal['finish']
    summary: str


class AgentAction(BaseModel):
    action: Annotated[ReadAction | FinishAction, Field(discriminator='kind')]


class ActionArgs(BaseModel):
    path: str | None = None
    start: int | None = None
    end: int | None = None
    summary: str | None = None


class FlatAgentAction(BaseModel):
    kind: Literal['read_file', 'finish']
    args: ActionArgs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--flat', action='store_true')
    args = parser.parse_args()
    calls = []
    def before(provider, model):
        if calls:
            raise RuntimeError('Probe permits exactly one provider request.')
        calls.append({'provider': provider, 'model': model})
    settings = replace(get_router_settings(), enable_failover=False, max_provider_retries=0,
                       max_output_tokens=512)
    router = ModelRouter(settings=settings, before_call=before)
    with capture_timings() as spans:
        try:
            result = router.route_normal('Return a read_file action for example.py, lines 1 through 10. This is a schema-only probe; do not execute anything.',
                                         schema=FlatAgentAction if args.flat else AgentAction, task_id='schema-probe')
            action = result.result.parsed
            passed = isinstance(action, AgentAction) and isinstance(action.action, ReadAction) and action.action.path == 'example.py'
            if args.flat:
                validated = ReadAction.model_validate({'kind': action.kind, **action.args.model_dump(exclude_none=True)})
                passed = validated.path == 'example.py' and validated.start == 1 and validated.end == 10
            report = {'accepted': passed, 'calls': calls, 'usage': result.usage.to_dict() if result.usage else None}
        except Exception as exc:
            report = {'accepted': False, 'calls': calls, 'error_type': type(exc).__name__,
                      'usage': None, 'note': 'Usage unavailable on rejected/failed response; not asserted zero.'}
    report['spans'] = spans
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
