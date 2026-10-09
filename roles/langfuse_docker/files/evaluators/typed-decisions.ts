type JsonObject = Record<string, unknown>;

type EvaluationContext = {
  observation: { output: unknown };
  experiment?: { itemExpectedOutput: unknown };
};

type Score = {
  name: string;
  value: number;
  dataType: "NUMERIC";
  comment?: string;
  metadata?: Record<string, unknown>;
};

function asObject(value: unknown): JsonObject {
  const parsed = typeof value === "string" ? JSON.parse(value) : value;
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Expected a JSON object for the typed decision result");
  }
  return parsed as JsonObject;
}

function answerLabel(answer: JsonObject): string | undefined {
  if (typeof answer.choice === "string") return answer.choice;
  if (typeof answer.level === "string") return answer.level;
  if (typeof answer.noul === "boolean") return String(answer.noul);
  if (typeof answer.label === "string") return answer.label;
  return undefined;
}

function numeric(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function brier(predicted: JsonObject, expected: JsonObject): number | undefined {
  const predictedKeys = Object.keys(predicted);
  const expectedKeys = Object.keys(expected);
  if (
    expectedKeys.length === 0 ||
    predictedKeys.length !== expectedKeys.length ||
    expectedKeys.some((key) => !predictedKeys.includes(key))
  ) {
    return undefined;
  }
  const pairs: Array<[number, number]> = [];
  let predictedTotal = 0;
  let expectedTotal = 0;
  for (const key of expectedKeys) {
    const p = numeric(predicted[key]);
    const q = numeric(expected[key]);
    if (p === undefined || q === undefined || p < 0 || p > 1 || q < 0 || q > 1) {
      return undefined;
    }
    pairs.push([p, q]);
    predictedTotal += p;
    expectedTotal += q;
  }
  const probabilityTolerance = 0.00001;
  const roundingTolerance = 0.05;
  if (
    Math.abs(predictedTotal - 1) > roundingTolerance ||
    Math.abs(expectedTotal - 1) > probabilityTolerance
  ) {
    return undefined;
  }
  // Rounded predictions (0.33 three times) are scored as the distribution they
  // round to, so the prediction is normalized before the squared error. A total
  // further from 1 than rounding explains is not a distribution and is skipped.
  let total = 0;
  for (const [p, q] of pairs) {
    total += (p / predictedTotal - q) ** 2;
  }
  return total;
}

function evaluate(ctx: EvaluationContext): { scores: Score[] } {
  if (!ctx.experiment) throw new Error("Typed-decisions scores require a dataset experiment");

  const expected = asObject(ctx.experiment.itemExpectedOutput);
  const output = asObject(ctx.observation.output);
  const answers = asObject(output.answers ?? output);
  let exact = 0;
  let exactCount = 0;
  let numericEquivalent = 0;
  let numericCount = 0;
  let brierTotal = 0;
  let brierCount = 0;
  const absoluteTolerance = 0.001;

  for (const [question, expectedValue] of Object.entries(expected)) {
    const reference = asObject(expectedValue);
    const predictedValue = answers[question];
    const predicted =
      predictedValue === undefined ? undefined : asObject(predictedValue);
    const expectedLabel = typeof reference.label === "string" ? reference.label : undefined;
    const predictedLabel = predicted === undefined ? undefined : answerLabel(predicted);
    if (expectedLabel !== undefined) {
      exactCount += 1;
      if (predictedLabel === expectedLabel) exact += 1;
    }

    if (reference.type === "score") {
      const expectedScore = numeric(reference.score);
      const predictedScore = predicted === undefined ? undefined : numeric(predicted.score);
      if (expectedScore !== undefined) {
        numericCount += 1;
        if (
          predictedScore !== undefined &&
          Math.abs(predictedScore - expectedScore) <= absoluteTolerance
        ) {
          numericEquivalent += 1;
        }
      }
    }

    if (reference.probabilities !== undefined) {
      // A missing or malformed prediction costs this item its Brier term only.
      let value: number | undefined;
      try {
        value = brier(asObject(predicted?.probabilities), asObject(reference.probabilities));
      } catch {
        value = undefined;
      }
      if (value !== undefined) {
        brierTotal += value;
        brierCount += 1;
      }
    }
  }

  if (exactCount === 0 || numericCount === 0 || brierCount === 0) {
    throw new Error("Output is missing typed answers, numeric scores, or probabilities");
  }

  return {
    scores: [
      {
        name: "typed_decision_exact_accuracy",
        value: exact / exactCount,
        dataType: "NUMERIC",
        metadata: { correct: exact, total: exactCount },
      },
      {
        name: "typed_decision_numeric_equivalence",
        value: numericEquivalent / numericCount,
        dataType: "NUMERIC",
        comment: `Absolute tolerance: ${absoluteTolerance}`,
        metadata: { equivalent: numericEquivalent, total: numericCount },
      },
      {
        name: "typed_decision_probability_brier",
        value: brierTotal / brierCount,
        dataType: "NUMERIC",
        comment: "Mean multiclass Brier score; lower is better.",
        metadata: { total: brierCount, lower_is_better: true },
      },
    ],
  };
}
