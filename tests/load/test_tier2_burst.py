"""Exercise the k6 module with native Node VM modules and network-free doubles."""

import json
from pathlib import Path
import subprocess

import pytest
import yaml

from hermes_agent._prompt_catalog import catalog_files


SCRIPT = Path(__file__).with_name("tier2-burst.js")
CATALOG_PIN = yaml.safe_load(SCRIPT.with_name("catalog.yml").read_text())["load_test_prompts_flake_ref"]
PROMPT_NAME = "tier-2-load-test-request.md"
HARNESS = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const config = JSON.parse(process.argv[2]);
const records = [], calls = [], metrics = {};
let clock = 0;
class Metric {
  constructor(name) { this.name = name; metrics[name] = []; }
  add(value, tags) { metrics[this.name].push({value, tags}); }
}
const env = {
  ROUTER_BASE_URL: 'https://', ROUTER_TIER2_KEY: 'fixture-tier2',
  ROUTER_TIER0_KEY: 'fixture-tier0', ROUTER_TIER2_MODEL: 'fixture-model',
  ROUTER_TIER0_MODEL: 'fixture-model', LOAD_RUN_ID: 'fixture-run',
  LOAD_PROMPT_FILE: 'fixture-catalog.md',
  ...config.env,
};
const scenario = {iterationInTest: 0};
const http = {post(url, body, params) {
  calls.push({url, body: JSON.parse(body), params}); clock += 25;
  if (config.transportError) throw new Error('transport failed');
  return {status: config.status ?? 200, error_code: config.errorCode ?? 0,
    json() {
      if (config.malformed) throw new Error('invalid JSON');
      return config.body ?? {choices: [{message: {content: '1 2'}, finish_reason: 'stop'}]};
    }};
}};
const sse = {open(url, params, callback) {
  calls.push({url, body: JSON.parse(params.body), params});
  const handlers = {};
  let closed = false;
  const client = {on(event, handler) {handlers[event] = handler;}, close() {closed = true;}};
  callback(client);
  for (const event of config.events || []) {
    if (closed) break;
    clock = event.at;
    if (event.error) handlers.error();
    else handlers.event({data: event.data});
  }
  return {status: config.status ?? 200, error: config.responseError};
}};
const context = vm.createContext({__ENV: env, console: {log(value) {records.push(JSON.parse(value));}},
  open(path) {
    assert.equal(path, env.LOAD_PROMPT_FILE);
    if (!path || config.missingPrompt) throw new Error('catalog file unavailable');
    return config.prompt;
  },
  Date: class extends Date {static now() {return clock;}}});
const dependencies = {
  'k6/http': {default: http}, 'k6/x/sse': {default: sse},
  'k6/execution': {default: {scenario}}, 'k6/metrics': {Counter: Metric, Trend: Metric},
};
const source = fs.readFileSync(process.argv[3], 'utf8');
const module = new vm.SourceTextModule(source, {context});
await module.link(name => {
  const exports = dependencies[name];
  return new vm.SyntheticModule(Object.keys(exports), function () {
    for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
  }, {context});
});
if (config.missingPrompt || !env.LOAD_PROMPT_FILE) {
  await assert.rejects(module.evaluate(), /catalog file unavailable/);
  assert.equal(calls.length, 0);
  process.exit(0);
}
await module.evaluate();
const api = module.namespace;
const count = name => metrics[name].reduce((sum, point) => sum + point.value, 0);
"""


def run_case(config, assertions):
    config = {"prompt": catalog_files(CATALOG_PIN, "automation/testing")[PROMPT_NAME], **config}
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "--input-type=module", "-",
         json.dumps(config), str(SCRIPT)],
        input=HARNESS + assertions,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_burst_accounts_for_all_requests_and_preserves_policy():
    run_case({}, r"""
for (let i = 0; i < 1000; i++) {scenario.iterationInTest = i; api.tier2();}
assert.equal(new Set(records.map(row => row.request_id)).size, 1000);
assert.equal(count('tier2_attempted'), 1000);
assert.equal(count('tier2_completed'), 1000);
assert.equal(count('tier2_errors'), 0);
assert.equal(count('tier2_timeouts'), 0);
assert.equal(count('tier2_outcomes'), 1000);
assert.equal(metrics.tier2_latency_ms.length, 1000);
assert.ok(records.every(row => row.status === 200 && row.latency_ms === 25));
for (const call of calls) {
  assert.equal(call.body.messages[0].content,
    config.prompt.replace(/^---\n[\s\S]*?\n---\n/, '').replaceAll('${id}', call.body.metadata.request_id));
  assert.equal(call.params.timeout, '12h');
  assert.equal(call.params.headers.Authorization, 'Bearer fixture-tier2');
  assert.deepEqual(Array.from(call.body.metadata.tags), ['&tier-2', '&local']);
  assert.equal(call.body.metadata.privacy_class, 'private');
  assert.equal(call.body.metadata.spend_logs_metadata.request_id, call.body.metadata.request_id);
  assert.equal(call.body.stream, false);
  assert.equal(call.body.cache['no-cache'], true);
}
assert.equal(api.options.scenarios.tier2.rate, 1000);
assert.equal(api.options.scenarios.tier2.timeUnit, '60s');
assert.equal(api.options.scenarios.tier2.duration, '60s');
assert.equal(api.options.scenarios.tier2.preAllocatedVUs, 1000);
assert.equal(api.options.scenarios.tier2.gracefulStop, '12h1m');
""")


@pytest.mark.parametrize("config, timed_out", [
    ({"status": 429}, 0),
    ({"status": 504}, 1),
    ({"status": 0, "errorCode": 1050}, 1),
    ({"malformed": True}, 0),
    ({"transportError": True}, 0),
    ({"body": {"error": {"message": "fixture error"}}}, 0),
    ({"body": {"choices": []}}, 0),
    ({"body": {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}}, 0),
])
def test_burst_rejects_unsuccessful_responses(config, timed_out):
    run_case(config, f"""
api.tier2();
assert.equal(count('tier2_outcomes'), 1);
assert.equal(count('tier2_completed'), 0);
assert.equal(count('tier2_errors'), 1);
assert.equal(count('tier2_timeouts'), {timed_out});
assert.equal(records.length, 1);
assert.equal(calls.length, 1);
""")


def frame(at, **choice):
    return {"at": at, "data": json.dumps({"choices": [choice]})}


STREAM = [
    {"at": 5, "data": ""},
    frame(10, delta={"role": "assistant", "content": ""}),
    frame(75, delta={"content": "1"}),
    frame(90, delta={"content": " 2"}),
    frame(100, delta={}, finish_reason="stop"),
    {"at": 110, "data": "[DONE]"},
]


def test_stream_measures_first_token_after_keepalive_and_role_frame():
    run_case({"events": STREAM}, r"""
api.tier0();
assert.equal(calls[0].body.messages[0].content,
  config.prompt.replace(/^---\n[\s\S]*?\n---\n/, '').replaceAll('${id}', calls[0].body.metadata.request_id));
assert.equal(count('tier0_completed'), 1);
assert.equal(count('tier0_errors'), 0);
assert.equal(metrics.tier0_ttft_ms.length, 1);
assert.equal(metrics.tier0_ttft_ms[0].value, 75);
assert.equal(records[0].ttft_ms, 75);
assert.equal(calls[0].params.headers.Authorization, 'Bearer fixture-tier0');
assert.equal(calls[0].params.method, 'POST');
assert.equal(calls[0].body.stream, true);
assert.deepEqual(calls[0].body.metadata.tags, ['&tier-0', '&local']);
""")


@pytest.mark.parametrize("config", [
    {"events": STREAM[:-1]},
    {"events": [*STREAM[:2], STREAM[-1]]},
    {"events": [{"at": 1, "data": "not JSON"}, *STREAM]},
    {"events": [{"at": 1, "error": True}]},
    {"events": STREAM, "status": 503},
    {"events": STREAM, "responseError": "fixture transport failure"},
])
def test_stream_rejects_errors_and_missing_tokens_or_terminator(config):
    run_case(config, """
api.tier0();
assert.equal(count('tier0_completed'), 0);
assert.equal(count('tier0_errors'), 1);
""")


def test_setup_refuses_missing_configuration_without_network_calls():
    run_case({"env": {"ROUTER_TIER2_KEY": ""}}, """
assert.throws(() => api.setup(), /Set ROUTER_TIER2_KEY/);
assert.equal(calls.length, 0);
""")


def test_setup_refuses_invalid_origin_without_printing_inputs():
    run_case({}, """
assert.throws(() => api.setup(), /HTTPS origin/);
assert.equal(records.length, 0);
assert.equal(calls.length, 0);
""")


@pytest.mark.parametrize("config", [{"missingPrompt": True}, {"env": {"LOAD_PROMPT_FILE": ""}}])
def test_missing_catalog_fails_before_requests(config):
    run_case(config, "")


def test_prompt_substitution_preserves_all_other_bytes():
    run_case({"prompt": "---\ntitle: fixture\n---\n\n${id} ${other} ${id}\n"}, r"""
api.tier2();
assert.equal(calls[0].body.messages[0].content,
  '\nfixture-run-tier2-0 ${other} fixture-run-tier2-0\n');
""")


def test_renovate_tracks_both_catalog_pins():
    run_case({}, """
const root = new URL('../../', `file://${process.argv[3]}`);
const renovate = JSON.parse(fs.readFileSync(new URL('renovate.json', root), 'utf8'));
const manager = renovate.customManagers.find(manager => manager.matchStrings.some(
  pattern => pattern.includes('ai-llm-prompts')));
for (const file of ['tests/load/catalog.yml', 'roles/hermes_agent/defaults/main/10-installer-and-bundles.yml']) {
  assert.ok(manager.managerFilePatterns.some(pattern => new RegExp(pattern.slice(1, -1)).test(file)), file);
  const match = new RegExp(manager.matchStrings[0]).exec(fs.readFileSync(new URL(file, root), 'utf8'));
  assert.equal(match.groups.depName, 'dryvist/ai-llm-prompts');
  assert.match(match.groups.currentValue, /^v\\d+\\.\\d+\\.\\d+$/);
  assert.match(match.groups.currentDigest, /^[0-9a-f]{40}$/);
}
""")
