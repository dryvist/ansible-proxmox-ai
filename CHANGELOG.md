# Changelog

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
