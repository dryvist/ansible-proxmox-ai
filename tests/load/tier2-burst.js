import http from 'k6/http';
import sse from 'k6/x/sse';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

const total = 1000;
const patience = '12h';
const grace = '12h1m';
const base = (__ENV.ROUTER_BASE_URL || '').replace(/\/$/, '');
const run = __ENV.LOAD_RUN_ID;
const prompt = open(__ENV.LOAD_PROMPT_FILE).replace(/^---\n[\s\S]*?\n---\n/, '');
const attempted = new Counter('tier2_attempted');
const completed = new Counter('tier2_completed');
const errors = new Counter('tier2_errors');
const timeouts = new Counter('tier2_timeouts');
const outcomes = new Counter('tier2_outcomes');
const latency = new Trend('tier2_latency_ms', true);
const streamingErrors = new Counter('tier0_errors');
const streamingCompleted = new Counter('tier0_completed');
const ttft = new Trend('tier0_ttft_ms', true);

export const options = {
  scenarios: {
    tier2: {
      executor: 'constant-arrival-rate', exec: 'tier2', rate: total,
      timeUnit: '60s', duration: '60s', preAllocatedVUs: total,
      maxVUs: total, gracefulStop: grace,
    },
    tier0: {
      executor: 'constant-arrival-rate', exec: 'tier0', rate: 1,
      timeUnit: '5s', duration: '50s', startTime: '10s',
      preAllocatedVUs: 10, maxVUs: 10, gracefulStop: grace,
    },
  },
  maxRedirects: 0,
  summaryTrendStats: ['min', 'med', 'p(95)', 'max'],
  thresholds: {
    tier2_attempted: [`count==${total}`],
    tier2_outcomes: [`count==${total}`],
    tier2_completed: [`count==${total}`],
    tier2_errors: ['count==0'],
    tier2_timeouts: ['count==0'],
    tier0_completed: ['count==10'],
    tier0_errors: ['count==0'],
    tier0_ttft_ms: ['p(95)>0'],
    dropped_iterations: ['count==0'],
  },
};

export function setup() {
  for (const name of ['ROUTER_BASE_URL', 'ROUTER_TIER2_KEY', 'ROUTER_TIER0_KEY',
    'ROUTER_TIER2_MODEL', 'ROUTER_TIER0_MODEL', 'LOAD_RUN_ID']) {
    if (!__ENV[name]) throw new Error(`Set ${name}; see tests/load/README.md`);
  }
  if (!/^https:\/\/[^/?#@\s]+$/.test(base)) {
    throw new Error('ROUTER_BASE_URL must be an HTTPS origin without credentials or a path');
  }
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(run)) {
    throw new Error('LOAD_RUN_ID must be 1-64 letters, digits, underscores, or hyphens');
  }
  console.log(JSON.stringify({ event: 'start', run_id: run, at: new Date().toISOString() }));
}

function request(tier) {
  const id = `${run}-tier${tier}-${exec.scenario.iterationInTest}`;
  return {
    id,
    body: JSON.stringify({
      model: __ENV[`ROUTER_TIER${tier}_MODEL`],
      messages: [{ role: 'user', content: prompt.replaceAll('${id}', id) }],
      max_tokens: 512,
      stream: tier === 0,
      cache: { 'no-cache': true },
      metadata: {
        tags: [`&tier-${tier}`, '&local'],
        agent: 'acceptance-load', tier, privacy_class: 'private',
        session_id: run,
        request_id: id,
        spend_logs_metadata: { run_id: run, request_id: id, tier },
      },
    }),
    params: {
      headers: {
        Authorization: `Bearer ${__ENV[`ROUTER_TIER${tier}_KEY`]}`,
        'Content-Type': 'application/json',
      },
      timeout: patience,
      redirects: 0,
      tags: { name: 'chat/completions', tier: String(tier), request_id: id },
    },
  };
}

export function tier2() {
  const req = request(2);
  const started = Date.now();
  attempted.add(1);
  let response;
  let valid = false;
  let timedOut = false;
  try {
    response = http.post(`${base}/v1/chat/completions`, req.body, req.params);
    timedOut = response.error_code === 1050 || [408, 504].includes(response.status);
    if (response.status === 200) {
      const body = response.json();
      const choice = body.choices?.[0];
      valid = !body.error && typeof choice?.message?.content === 'string'
        && choice.message.content.length > 0 && Boolean(choice.finish_reason);
    }
  } catch (_) {
    // A malformed response or transport exception is a terminal error for this id.
  }
  const tags = { request_id: req.id, status: String(response?.status || 0) };
  const elapsed = Date.now() - started;
  completed.add(valid ? 1 : 0);
  errors.add(valid ? 0 : 1);
  timeouts.add(timedOut ? 1 : 0);
  outcomes.add(1, tags);
  latency.add(elapsed, tags);
  console.log(JSON.stringify({
    event: 'tier2_result', request_id: req.id, status: response?.status || 0,
    latency_ms: elapsed, completed: valid, timeout: timedOut,
    error_code: response?.error_code || 0, at: new Date().toISOString(),
  }));
}

export function tier0() {
  const req = request(0);
  const started = Date.now();
  let firstToken = null;
  let done = false;
  let finished = false;
  let failed = false;
  let response;
  try {
    response = sse.open(`${base}/v1/chat/completions`, {
      ...req.params, method: 'POST', body: req.body,
    }, (client) => {
      client.on('event', (event) => {
        if (!event.data) return;
        if (event.data === '[DONE]') {
          done = true;
          client.close();
          return;
        }
        try {
          const chunk = JSON.parse(event.data);
          if (chunk.error) failed = true;
          for (const choice of chunk.choices || []) {
            // Role-only frames and keepalives are not generated tokens.
            const token = choice.delta?.content || choice.delta?.reasoning_content;
            if (typeof token === 'string' && token.length > 0 && firstToken === null) {
              firstToken = Date.now() - started;
              ttft.add(firstToken, { request_id: req.id });
            }
            if (choice.finish_reason) finished = true;
          }
        } catch (_) {
          failed = true;
        }
      });
      client.on('error', () => { failed = true; client.close(); });
    });
  } catch (_) {
    failed = true;
  }
  const valid = response?.status === 200 && !response.error && !failed
    && firstToken !== null && done && finished;
  streamingCompleted.add(valid ? 1 : 0);
  streamingErrors.add(valid ? 0 : 1);
  console.log(JSON.stringify({
    event: 'tier0_result', request_id: req.id, status: response?.status || 0,
    ttft_ms: firstToken, latency_ms: Date.now() - started,
    completed: valid, at: new Date().toISOString(),
  }));
}

export function teardown() {
  console.log(JSON.stringify({ event: 'end', run_id: run, at: new Date().toISOString() }));
}
