# Changelog

## [0.11.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.10.0...v0.11.0) (2026-07-21)


### Features

* **hermes_agent:** add work-supply kanban cards (anomaly-hunt, docs-study, ai-news, daily-innovation, app-seeding) ([#115](https://github.com/dryvist/ansible-proxmox-ai/issues/115)) ([6dacbc6](https://github.com/dryvist/ansible-proxmox-ai/commit/6dacbc63739094734fdb46863e898ddbd4a94c82))
* **hermes_agent:** dial Splunk MCP through the shared agentgateway route ([#118](https://github.com/dryvist/ansible-proxmox-ai/issues/118)) ([ecd1dc7](https://github.com/dryvist/ansible-proxmox-ai/commit/ecd1dc751e5f24f90b52bed66f34736ad5b5ef21))
* **hermes_agent:** harden kanban card prompts for weak-model robustness ([#119](https://github.com/dryvist/ansible-proxmox-ai/issues/119)) ([535cff5](https://github.com/dryvist/ansible-proxmox-ai/commit/535cff5979cc1c7dae98cdfe56607473dac28c71))


### Bug Fixes

* **hermes_agent:** lazy-eval enqueuer name/selector defaults for 2.21 ([#116](https://github.com/dryvist/ansible-proxmox-ai/issues/116)) ([8f73132](https://github.com/dryvist/ansible-proxmox-ai/commit/8f731320976650e02c6703e7d064352686def135))
* **hermes_agent:** set kanban.default_assignee so ready cards dispatch ([#117](https://github.com/dryvist/ansible-proxmox-ai/issues/117)) ([b8c462c](https://github.com/dryvist/ansible-proxmox-ai/commit/b8c462cdc82399dbb08560ea0ea4463bbad97d86))

## [0.10.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.9.0...v0.10.0) (2026-07-21)


### Features

* **hermes_agent:** ping external deadman on healthy brain probe ([#102](https://github.com/dryvist/ansible-proxmox-ai/issues/102)) ([3a5aaa4](https://github.com/dryvist/ansible-proxmox-ai/commit/3a5aaa42c16b3ed7905bade9f946a44b9adb8e11))


### Bug Fixes

* **hermes_agent:** remove superseded agentic crons on kanban converge ([#105](https://github.com/dryvist/ansible-proxmox-ai/issues/105)) ([66e241e](https://github.com/dryvist/ansible-proxmox-ai/commit/66e241e480382605aabe5130b2eb653f76cbe9c6))
* **hermes:** point brain-watchdog ntfy page at the correct DNS zone ([#101](https://github.com/dryvist/ansible-proxmox-ai/issues/101)) ([f65f7d2](https://github.com/dryvist/ansible-proxmox-ai/commit/f65f7d29eb035bafa61a8a4307862fdc1e8608f5))

## [0.9.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.8.0...v0.9.0) (2026-07-21)


### Features

* **hermes_agent:** decouple hermes brain from ai-default (behavior-neutral pin) ([#59](https://github.com/dryvist/ansible-proxmox-ai/issues/59)) ([d77bc52](https://github.com/dryvist/ansible-proxmox-ai/commit/d77bc525bf6a3505589cb4cf8184378d2bcfd698))
* **hermes:** migrate cron fleet to Kanban cards with script-only enqueuers ([#97](https://github.com/dryvist/ansible-proxmox-ai/issues/97)) ([4f5e848](https://github.com/dryvist/ansible-proxmox-ai/commit/4f5e84842a8c3deeae4e2efd2b8ea3d83dee2d9d)), closes [#83](https://github.com/dryvist/ansible-proxmox-ai/issues/83)
* **llm_router:** optional same-model standby brain backend (default off) ([#58](https://github.com/dryvist/ansible-proxmox-ai/issues/58)) ([0de42ac](https://github.com/dryvist/ansible-proxmox-ai/commit/0de42acf575930514cd99d2fe63d814c4ed3598e))

## [0.8.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.7.1...v0.8.0) (2026-07-20)


### Features

* **hermes_agent:** set agent log level to DEBUG for pre-error context ([#92](https://github.com/dryvist/ansible-proxmox-ai/issues/92)) ([0b36733](https://github.com/dryvist/ansible-proxmox-ai/commit/0b367335e7fd4fd89263d7762e0e5664d9a37a39))


### Bug Fixes

* **hermes_agent:** halve reserved output tokens to widen usable context ([#91](https://github.com/dryvist/ansible-proxmox-ai/issues/91)) ([644a825](https://github.com/dryvist/ansible-proxmox-ai/commit/644a825117b96b02f3bf163628349defd883ff58))
* **hermes:** restart the gateway on resume, before the cron fleet wakes ([#93](https://github.com/dryvist/ansible-proxmox-ai/issues/93)) ([bb81405](https://github.com/dryvist/ansible-proxmox-ai/commit/bb81405aa073bd0c03c37b29612fe823e1598274))

## [0.7.1](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.7.0...v0.7.1) (2026-07-20)


### Bug Fixes

* **hermes_agent:** own gateway Restart=always in the unit; drop stale policy drop-in ([#87](https://github.com/dryvist/ansible-proxmox-ai/issues/87)) ([1f1d5ff](https://github.com/dryvist/ansible-proxmox-ai/commit/1f1d5ff0207a7b2f7a49c7c10a3f4e84455435d3))
* **hermes_agent:** pin session_reset policy + always-restart gateway ([#85](https://github.com/dryvist/ansible-proxmox-ai/issues/85)) ([99b8b67](https://github.com/dryvist/ansible-proxmox-ai/commit/99b8b6775d1a63339fed8e2144d9eafac81c4fed))
* **qdrant:** stop pinning molecule verify to a full image tag ([#84](https://github.com/dryvist/ansible-proxmox-ai/issues/84)) ([29c694f](https://github.com/dryvist/ansible-proxmox-ai/commit/29c694fb696682c74b164e7abcc184e1529ee338))

## [0.7.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.6.0...v0.7.0) (2026-07-20)


### Features

* **hermes_agent:** forward hermes unit logs to dedicated hermes index ([#74](https://github.com/dryvist/ansible-proxmox-ai/issues/74)) ([132aa89](https://github.com/dryvist/ansible-proxmox-ai/commit/132aa89f291d730372c4a99c8067532be2a6b32b))

## [0.6.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.5.0...v0.6.0) (2026-07-20)


### Features

* **ai:** make the fabric brain an OpenBao runtime var, not a converge value ([#49](https://github.com/dryvist/ansible-proxmox-ai/issues/49)) ([ec4ad81](https://github.com/dryvist/ansible-proxmox-ai/commit/ec4ad81a429df565da3bdf040843613b3db45dbf))
* consume central application prompts (pinned ai-llm-prompts catalog) ([#56](https://github.com/dryvist/ansible-proxmox-ai/issues/56)) ([b4be970](https://github.com/dryvist/ansible-proxmox-ai/commit/b4be970a112c52311b2fd067a6a5ba599fd3a58c))
* **hermes_agent:** alert when the brain-health watchdog itself crashes ([f0ecfe8](https://github.com/dryvist/ansible-proxmox-ai/commit/f0ecfe802e35e997b598a0250ddbf39a26ff7412))
* **hermes_agent:** brain-watchdog sustained-flap escalation (INC-17083) ([#60](https://github.com/dryvist/ansible-proxmox-ai/issues/60)) ([814e2a8](https://github.com/dryvist/ansible-proxmox-ai/commit/814e2a8ec5ec6702b298886513008c2d85603930))
* **hermes_agent:** delta-discipline cron prompts and watchdog flap coalescing ([5125af5](https://github.com/dryvist/ansible-proxmox-ai/commit/5125af596d44bf785d73a9af584fdeeb0f854bda))
* **hermes:** 3-tier Slack routing + daily summary + proactive Zammad review ([#51](https://github.com/dryvist/ansible-proxmox-ai/issues/51)) ([9c20309](https://github.com/dryvist/ansible-proxmox-ai/commit/9c2030951a195c8fdf4b03bd2ea6bb7c8df99c5b))
* **hermes:** bound the kanban dispatcher and open cron card-creation ([#52](https://github.com/dryvist/ansible-proxmox-ai/issues/52)) ([f380aec](https://github.com/dryvist/ansible-proxmox-ai/commit/f380aec57a1beadfc6232784316a7b0b1f7d3571))
* **llm_router:** openrouter/* dynamic tier — any current model, budget-gated ([#55](https://github.com/dryvist/ansible-proxmox-ai/issues/55)) ([4dd46ee](https://github.com/dryvist/ansible-proxmox-ai/commit/4dd46ee675f0ad0f3eb507a681506923dc3d32c5))
* **llm_router:** register the two-Mac cluster brain behind the gate's :11440 site ([4304255](https://github.com/dryvist/ansible-proxmox-ai/commit/43042558476da83f5204ff7b081439a05725854e))
* Qwen3-Next-80B-A3B-Instruct becomes the fleet brain + compression model ([7d63a60](https://github.com/dryvist/ansible-proxmox-ai/commit/7d63a609a63a99086a4e0f4ebcc8e943c98f4bfb))


### Bug Fixes

* **ai:** point fabric default + Hermes compression at deepseek-v4-flash (stability stopgap); hourly Slack heartbeat ([#44](https://github.com/dryvist/ansible-proxmox-ai/issues/44)) ([74e54a4](https://github.com/dryvist/ansible-proxmox-ai/commit/74e54a4db1fbe97912978306390f78cb47a21c4d))
* **hermes_agent:** correct role's repo-of-record after ansible-proxmox-apps split ([92ea41b](https://github.com/dryvist/ansible-proxmox-ai/commit/92ea41b7ca8d1062d6d1353e310185b71e1bda6c))
* **hermes:** seed cron jobs via argv — prompt punctuation broke cmd splitting ([#53](https://github.com/dryvist/ansible-proxmox-ai/issues/53)) ([8b020fb](https://github.com/dryvist/ansible-proxmox-ai/commit/8b020fbf35139a0bbf1405b0d1a19c57a97cf375))
* **hindsight_docker:** size the readiness gate for a cold start ([303b55d](https://github.com/dryvist/ansible-proxmox-ai/commit/303b55d84b5e114f861194c145f7dac0b79bf9b5))
* **hindsight:** run MCP stateless so the HA pool needs no session affinity ([#42](https://github.com/dryvist/ansible-proxmox-ai/issues/42)) ([d07d480](https://github.com/dryvist/ansible-proxmox-ai/commit/d07d4802ed78b7dc2c0386e6c962af33d65190fb))
* **hindsight:** wire up the memory service that never ran ([be6a92a](https://github.com/dryvist/ansible-proxmox-ai/commit/be6a92a690e45ce3a656ae97dfdccf223cde2796))

## [0.5.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.4.0...v0.5.0) (2026-07-17)


### Features

* **agentgateway:** drop transitional /&lt;name&gt;/mcp route aliases ([325ec2c](https://github.com/dryvist/ansible-proxmox-ai/commit/325ec2c3f399fc91927372f83ce49da80a9cbfea))
* **agentgateway:** read-only docs-search MCP route over the RAG collection ([c308a4b](https://github.com/dryvist/ansible-proxmox-ai/commit/c308a4b8af5fa3a9f2406e8c7e69206dfef21792)), closes [#22](https://github.com/dryvist/ansible-proxmox-ai/issues/22)


### Bug Fixes

* **agentgateway:** embed docs-search queries via the router, not in-container ([85c2f96](https://github.com/dryvist/ansible-proxmox-ai/commit/85c2f96ccb76cfc7b7ab87bd74187a9bd91ab02a)), closes [#22](https://github.com/dryvist/ansible-proxmox-ai/issues/22)
* **agentgateway:** pin the docs-search server path to /mcp/ ([72a587f](https://github.com/dryvist/ansible-proxmox-ai/commit/72a587f91dd7afc548bfde29b4c97ff7b9e59d20))
* **agentgateway:** search the docs collection's unnamed dense vector ([fd68cee](https://github.com/dryvist/ansible-proxmox-ai/commit/fd68ceec0e778c55df9c4dcc2d5b6a63302960bb))
* **hindsight:** publish bao_apps_secrets so the memory role resolves its credentials ([#30](https://github.com/dryvist/ansible-proxmox-ai/issues/30)) ([3ba4851](https://github.com/dryvist/ansible-proxmox-ai/commit/3ba4851e815a126c150bb77b9204a3b483bcc15e))
* **llamaindex:** raise the indexer's Qdrant client timeout ([bb32c61](https://github.com/dryvist/ansible-proxmox-ai/commit/bb32c614900e1d3adf7be2d154b717f501a82ef3))

## [0.4.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.3.0...v0.4.0) (2026-07-17)


### ⚠ BREAKING CHANGES

* **agentgateway:** capability-named routes without the /mcp suffix

### Features

* **agentgateway,hermes:** point memory at the standalone HA Hindsight service ([#14](https://github.com/dryvist/ansible-proxmox-ai/issues/14)) ([52ec896](https://github.com/dryvist/ansible-proxmox-ai/commit/52ec896a6bbf84db8ae0aefe8a8714bd6a79d183))
* **agentgateway:** capability-named routes without the /mcp suffix ([1fdab1f](https://github.com/dryvist/ansible-proxmox-ai/commit/1fdab1f58f238225ad6e9300dcfc98d69d7f3e78))
* **hindsight_docker:** Hindsight agent-memory service role + molecule scenario ([#13](https://github.com/dryvist/ansible-proxmox-ai/issues/13)) ([2dc5a68](https://github.com/dryvist/ansible-proxmox-ai/commit/2dc5a685b5524e41e8ace22009371847e28c40b6))
* **openbao_secrets:** fetch the apps/hindsight credentials for hindsight_docker ([#23](https://github.com/dryvist/ansible-proxmox-ai/issues/23)) ([2853dba](https://github.com/dryvist/ansible-proxmox-ai/commit/2853dbacc9da410d88d6d5047d7df07cdb425ba0))

## [0.3.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.2.0...v0.3.0) (2026-07-17)


### Features

* self-sufficient deploy orchestration (site.yml + dynamic inventory) ([#17](https://github.com/dryvist/ansible-proxmox-ai/issues/17)) ([dd923a7](https://github.com/dryvist/ansible-proxmox-ai/commit/dd923a7a6d4699dd87a316007a9cf463a12e72b7))


### Bug Fixes

* **rag:** size embeddings physical batch to ctx; tune indexer chunks ([ea2bece](https://github.com/dryvist/ansible-proxmox-ai/commit/ea2bece844f159e45b85640bbff3b50fd2dd926c))

## [0.2.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.1.0...v0.2.0) (2026-07-17)


### ⚠ BREAKING CHANGES

* **llm_router:** literal model_group_aliases for all consumer aliases; ban duplicates ([#3](https://github.com/dryvist/ansible-proxmox-ai/issues/3))

### Features

* **agentgateway,hermes:** front Qdrant with an MCP route agents can dial ([#7](https://github.com/dryvist/ansible-proxmox-ai/issues/7)) ([b41ecfa](https://github.com/dryvist/ansible-proxmox-ai/commit/b41ecfa18f3ff281e0bce418515ea801f9b226db))


### Bug Fixes

* **ci:** import docker_engine role required by *_docker role deps ([#5](https://github.com/dryvist/ansible-proxmox-ai/issues/5)) ([fa3c1bd](https://github.com/dryvist/ansible-proxmox-ai/commit/fa3c1bd4f9a9d95211f0ccb49b8fdeac38dc9ada))
* **ci:** prove and repair pull_request CI wiring on first PR ([ba44e93](https://github.com/dryvist/ansible-proxmox-ai/commit/ba44e93de39b8615e7209cb384cc5359a2acc7b8))
* **hermes_agent:** gateway cwd must be HERMES_HOME for the memory plugin ([#8](https://github.com/dryvist/ansible-proxmox-ai/issues/8)) ([5ea133c](https://github.com/dryvist/ansible-proxmox-ai/commit/5ea133cf9d1191eba8b9ff128908a1514675c10a))
* **hermes_agent:** pin hindsight-client to the plugin's required version ([#4](https://github.com/dryvist/ansible-proxmox-ai/issues/4)) ([5a695ef](https://github.com/dryvist/ansible-proxmox-ai/commit/5a695efc2cc16648f190abca043a0bde0e6cb8ee))
* **llm_router:** compress-death assert accepts literal model_group_aliases ([#9](https://github.com/dryvist/ansible-proxmox-ai/issues/9)) ([a9ca335](https://github.com/dryvist/ansible-proxmox-ai/commit/a9ca3353a89edda5402baf582d9413b6b99c3a30))


### Refactoring

* **llm_router:** literal model_group_aliases for all consumer aliases; ban duplicates ([#3](https://github.com/dryvist/ansible-proxmox-ai/issues/3)) ([df217c2](https://github.com/dryvist/ansible-proxmox-ai/commit/df217c241988f61b4b7c4892ac80f587b6be8715))
