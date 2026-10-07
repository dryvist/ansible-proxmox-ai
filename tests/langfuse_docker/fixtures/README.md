# Langfuse API response fixtures

`api-response-shapes.json` preserves sanitized response shapes for dataset,
dataset-items, and evaluator GET endpoints. String values use placeholders.
Replay tests exercise these responses before any local fixture write.

Success, absent-object, and drift cases use synthetic API objects populated
from the pinned local dataset and evaluator source. These cases exercise the
production tasks; their healthy responses are synthetic.
