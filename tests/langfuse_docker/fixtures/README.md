# Langfuse API response fixtures

`api-response-shapes.json` preserves sanitized response shapes for dataset,
dataset-items, and evaluator GET endpoints. String values use placeholders.
Replay tests exercise these responses before any local fixture write.

Success, absent-object, and drift cases use synthetic API objects populated
from the pinned local dataset and evaluator source. These cases exercise the
production tasks; their healthy responses are synthetic.

The native reconciliation fixture follows Langfuse 4.70.0's runtime
[cursor schema](https://github.com/langfuse/langfuse/blob/v4.70.0/web/src/features/public-api/types/evaluation/evaluators.ts):
the initial request omits the cursor, and subsequent requests return its opaque
base64url value unchanged. Terminal responses omit the next cursor.

Judge mappings follow the runtime
[mapping schema](https://github.com/langfuse/langfuse/blob/v4.70.0/web/src/features/public-api/types/evaluation/publicEvalsContract.ts):
`jsonPath` is an optional, non-null string. Whole-source mappings omit it.
