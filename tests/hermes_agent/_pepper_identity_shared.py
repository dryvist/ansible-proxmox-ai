"""Shared inventory expression resolver for offline Pepper identity tests."""
import re
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

from _role_files import role_defaults

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'roles/hermes_agent'


def resolve(name, context):
    values = role_defaults(ROLE)
    for path in sorted((ROOT / 'inventory/group_vars/hermes_agent_group').glob('*.yml')):
        values.update(yaml.safe_load(path.read_text()))
    values.update(context)
    env = Environment(undefined=StrictUndefined)
    env.filters['mandatory'] = lambda value, message='': value
    env.filters.update(bool=bool, trim=lambda x: str(x).strip())
    env.globals['lookup'] = lambda kind, key: context.get('environment', {}).get(key, '')

    # Resolve dependencies explicitly without evaluating unrelated defaults
    # that require live infrastructure.
    def evaluate(key, stack=()):
        if key in context:
            return context[key]
        value = values[key]
        if not isinstance(value, str) or '{{' not in value:
            return value
        deps = set(re.findall(r'\bhermes_agent_\w+', value)) - {key}
        deps.update(set(re.findall(r'\b(?:bao_local_llm_secrets|bao_apps_secrets|openbao_secrets_by_domain)\b', value)))
        ctx = dict(context)
        for dep in deps:
            if dep not in stack and dep in values:
                ctx[dep] = evaluate(dep, (*stack, key))
        return env.from_string(value).render(ctx)

    return evaluate(name)
