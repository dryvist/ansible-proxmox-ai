# Changelog

## [0.22.1](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.22.0...v0.22.1) (2026-08-02)


### Bug Fixes

* **hermes:** resolve the Vikunja bridge token bao-first ([#286](https://github.com/dryvist/ansible-proxmox-ai/issues/286)) ([023bc93](https://github.com/dryvist/ansible-proxmox-ai/commit/023bc93ffcc212d2e78a25c81bbb9d73f5d93cee))

## [0.22.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.21.1...v0.22.0) (2026-08-02)


### Features

* **hermes_agent:** lift fleet-health off the throughput throttle ([#305](https://github.com/dryvist/ansible-proxmox-ai/issues/305)) ([179d71c](https://github.com/dryvist/ansible-proxmox-ai/commit/179d71c639942047b89825604d2df5bc2d66925c))


### Bug Fixes

* **inventory:** resolve ai_llm_concurrency when the artifact predates the constant ([#302](https://github.com/dryvist/ansible-proxmox-ai/issues/302)) ([c673e2d](https://github.com/dryvist/ansible-proxmox-ai/commit/c673e2d64dfb27c835d48e265bb8af3516267384))

## [0.21.1](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.21.0...v0.21.1) (2026-08-02)


### Bug Fixes

* **hermes_agent:** close five wiring gaps degrading Hermes output ([#292](https://github.com/dryvist/ansible-proxmox-ai/issues/292)) ([7ae5116](https://github.com/dryvist/ansible-proxmox-ai/commit/7ae5116d3b321839d9c6efa0c52809d69010f5eb))
* **hermes:** re-enable the brain-health watchdog ([#296](https://github.com/dryvist/ansible-proxmox-ai/issues/296)) ([41468a8](https://github.com/dryvist/ansible-proxmox-ai/commit/41468a877eae36515241934a996c8b1981fb8d61))

## [0.21.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.20.0...v0.21.0) (2026-08-02)


### Features

* **agentgateway:** add the Nautobot IPAM/DCIM MCP route ([#291](https://github.com/dryvist/ansible-proxmox-ai/issues/291)) ([51ae3a8](https://github.com/dryvist/ansible-proxmox-ai/commit/51ae3a8c7b49c102e240d8cdf090252b6ab3aacc))
* **hermes:** add Hindsight to fabric_watchdog and document silent memory-failure paths ([#275](https://github.com/dryvist/ansible-proxmox-ai/issues/275)) ([eb729c8](https://github.com/dryvist/ansible-proxmox-ai/commit/eb729c87eba914cbff25eb07df343ce71c66cef4))
* **hermes:** make the news scout real — working search, interest-aware suggestions ([4f721d5](https://github.com/dryvist/ansible-proxmox-ai/commit/4f721d508f5c63f47a7163974f07da887071d67d))


### Bug Fixes

* **hermes_agent:** assert a profile with the Splunk skill declares the splunk MCP ([#288](https://github.com/dryvist/ansible-proxmox-ai/issues/288)) ([8dd900a](https://github.com/dryvist/ansible-proxmox-ai/commit/8dd900a6db905027526f8565cc0f221fb14d2141))
* **hermes:** audit the kanban/direct-cron fleet, fix a dangling memory-key bug ([#294](https://github.com/dryvist/ansible-proxmox-ai/issues/294)) ([7da704a](https://github.com/dryvist/ansible-proxmox-ai/commit/7da704a75580aeb6fd1557760141cdf9e135eccc))
* **hermes:** close the interactive Slack toolset gap in H-17 policy ([4454117](https://github.com/dryvist/ansible-proxmox-ai/commit/44541178590404b90d147e0d9692579ec7e9b29b))
* **hermes:** fail the converge when the delivered Splunk MCP token cannot authenticate ([b6f8c34](https://github.com/dryvist/ansible-proxmox-ai/commit/b6f8c3464fdd5cdbc1960923374389374bbad42b))
* **hermes:** hand the app-seeding card its endpoints instead of letting it invent them ([d690b18](https://github.com/dryvist/ansible-proxmox-ai/commit/d690b1879ca6f36a92535e771c43424395097f67))
* **hermes:** split Slack routing into four channels by observation path ([#274](https://github.com/dryvist/ansible-proxmox-ai/issues/274)) ([c3aae40](https://github.com/dryvist/ansible-proxmox-ai/commit/c3aae40d4293adff185b3658d243ab09ef1c9b73))
* **hermes:** stop judge errors from burning kanban goal turns ([#277](https://github.com/dryvist/ansible-proxmox-ai/issues/277)) ([b8d8232](https://github.com/dryvist/ansible-proxmox-ai/commit/b8d823244cb7bbddfe6ab6697d011a7cd5df8fa3))
* **inventory:** add llm-router-1/2/3 to the ssh-override guest list ([#285](https://github.com/dryvist/ansible-proxmox-ai/issues/285)) ([0caa77b](https://github.com/dryvist/ansible-proxmox-ai/commit/0caa77b18f086787de96eb79e0905c2fb45c77cf))
* **inventory:** add per-guest ssh override for LXC converges ([#278](https://github.com/dryvist/ansible-proxmox-ai/issues/278)) ([98eddee](https://github.com/dryvist/ansible-proxmox-ai/commit/98eddeed0c3de4e9fe2f69dfbd1c9f8dd720bd4e))
* **inventory:** derive ai_llm_concurrency from tofu_data instead of a literal ([#293](https://github.com/dryvist/ansible-proxmox-ai/issues/293)) ([d3128c2](https://github.com/dryvist/ansible-proxmox-ai/commit/d3128c2ba782817d64fa5a33785399b1413d887a))
* **llm_router:** give hermes-default automatic failover to the cluster brain ([aface19](https://github.com/dryvist/ansible-proxmox-ai/commit/aface1993eb7168066203b475a5a055fdc859cb1))
* **llm_router:** give the liveness wait a budget that fits actual startup ([7c1366d](https://github.com/dryvist/ansible-proxmox-ai/commit/7c1366d3ac3b91f73503796f4d8755187c18be39))
* **scripts:** add run-ansible.sh SSH-CA wrapper ([#290](https://github.com/dryvist/ansible-proxmox-ai/issues/290)) ([56330c4](https://github.com/dryvist/ansible-proxmox-ai/commit/56330c44b5a7c22637c9ea27e9d140e8a116e51a))

## [0.20.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.19.0...v0.20.0) (2026-07-30)


### Features

* **agent_guest:** bound the repo field and job resources; record why ([#262](https://github.com/dryvist/ansible-proxmox-ai/issues/262)) ([b185763](https://github.com/dryvist/ansible-proxmox-ai/commit/b1857639afb34fbec15bbd99b46cac796bfc16cd))

## [0.19.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.18.0...v0.19.0) (2026-07-30)


### Features

* **agent_guest:** migrate the third CLI from gemini-cli to agy ([#245](https://github.com/dryvist/ansible-proxmox-ai/issues/245)) ([#246](https://github.com/dryvist/ansible-proxmox-ai/issues/246)) ([0cdc1b8](https://github.com/dryvist/ansible-proxmox-ai/commit/0cdc1b8fd1e8db5fd4794f4a02d3ae2b8d5d4f66))
* **agent_guest:** pooled autonomous agent role — ai_runner successor ([#226](https://github.com/dryvist/ansible-proxmox-ai/issues/226)) ([4a5ca12](https://github.com/dryvist/ansible-proxmox-ai/commit/4a5ca12a94751e3f41a1f08548026850e12e3a43))
* **hermes_agent:** report error signatures, gate the kanban digest's quiet posts ([#228](https://github.com/dryvist/ansible-proxmox-ai/issues/228)) ([2c36c93](https://github.com/dryvist/ansible-proxmox-ai/commit/2c36c93d1d2f534b542cc6aae9c242eaae7114d4))
* **hermes:** give the splunk-admin domain its own channel, inert until created ([#257](https://github.com/dryvist/ansible-proxmox-ai/issues/257)) ([9e9f28a](https://github.com/dryvist/ansible-proxmox-ai/commit/9e9f28a7c15b4b4a3dc4cbbaf5cb895ac704a11b))
* **hermes:** make the heartbeat the quiet-day log of record, every 4h ([#249](https://github.com/dryvist/ansible-proxmox-ai/issues/249)) ([6fd76eb](https://github.com/dryvist/ansible-proxmox-ai/commit/6fd76eb06b5bfd141a63c0a7139db8378cd609a6))
* **squid:** forward-proxy role and agent-guest proxy wiring ([#229](https://github.com/dryvist/ansible-proxmox-ai/issues/229)) ([3ddfdb7](https://github.com/dryvist/ansible-proxmox-ai/commit/3ddfdb7fef80d50dcfa156e1e8989c1a27bd637e))


### Bug Fixes

* **agent_guest:** become via su — stock Debian ships no sudo ([#237](https://github.com/dryvist/ansible-proxmox-ai/issues/237)) ([438dcd1](https://github.com/dryvist/ansible-proxmox-ai/commit/438dcd16bfaa48aef73b7d51d1ccae326ca806f2))
* **agent_guest:** create the systemd drop-in directory before templating ([#231](https://github.com/dryvist/ansible-proxmox-ai/issues/231)) ([827dee7](https://github.com/dryvist/ansible-proxmox-ai/commit/827dee714b59fcfb373209222f16944a65e42602))
* **agent_guest:** drop ansible_managed from the gitleaks hook copy task ([#234](https://github.com/dryvist/ansible-proxmox-ai/issues/234)) ([e5049d5](https://github.com/dryvist/ansible-proxmox-ai/commit/e5049d59a919690b3ccaf9a22d93369661bab4c4))
* **agent_guest:** gemini settings — drop invalid yolo enum, pin auth type ([#244](https://github.com/dryvist/ansible-proxmox-ai/issues/244)) ([b4ba8bc](https://github.com/dryvist/ansible-proxmox-ai/commit/b4ba8bc276f3536c768dc389d47031d727426bac))
* **agent_guest:** move pool-return to its own play — never-tag inheritance ([#242](https://github.com/dryvist/ansible-proxmox-ai/issues/242)) ([55019f5](https://github.com/dryvist/ansible-proxmox-ai/commit/55019f5ef5077cb82de3dcf24d59f9c4bc4ce9ed))
* **agent_guest:** render the proxy drop-ins with template, not copy ([#230](https://github.com/dryvist/ansible-proxmox-ai/issues/230)) ([b51c489](https://github.com/dryvist/ansible-proxmox-ai/commit/b51c4892e6ae0f8212c3bf4dcb46683b8b37cf86))
* **agent_guest:** set su via task var — connection vars outrank keywords ([#238](https://github.com/dryvist/ansible-proxmox-ai/issues/238)) ([b155d1c](https://github.com/dryvist/ansible-proxmox-ai/commit/b155d1c87beb132aa153b3dfd88931266bebe907))
* **agent_guest:** su -s /bin/sh for the nologin cribl user ([#241](https://github.com/dryvist/ansible-proxmox-ai/issues/241)) ([036d66c](https://github.com/dryvist/ansible-proxmox-ai/commit/036d66c86f5870ca497a1f1250e0028dabbea7dd))
* **agent_guest:** use Cribl's rolling latest URL — the versioned layout 404s ([#240](https://github.com/dryvist/ansible-proxmox-ai/issues/240)) ([18640ec](https://github.com/dryvist/ansible-proxmox-ai/commit/18640ec2a31386dd825a5ba6c5d0f5fc30c26964))
* **hermes:** CRITICAL anomaly alerts reach #hermes-all, not just the DM ([#255](https://github.com/dryvist/ansible-proxmox-ai/issues/255)) ([39c6d09](https://github.com/dryvist/ansible-proxmox-ai/commit/39c6d094bbd827a7e977d3852580f48cf8273909))
* **hermes:** gate digest failures through the day ledger, and announce recovery ([#248](https://github.com/dryvist/ansible-proxmox-ai/issues/248)) ([297a52b](https://github.com/dryvist/ansible-proxmox-ai/commit/297a52b6dc2bfe7e7800aa0d180961cfce5aa610))
* **hermes:** make #hermes-all the log of record structurally, not by coincidence ([#256](https://github.com/dryvist/ansible-proxmox-ai/issues/256)) ([88387c7](https://github.com/dryvist/ansible-proxmox-ai/commit/88387c72e80c3edab27f8b7ec272faef39df3be2))
* **hermes:** stop citing a completion gate that does not exist ([#250](https://github.com/dryvist/ansible-proxmox-ai/issues/250)) ([4ed7370](https://github.com/dryvist/ansible-proxmox-ai/commit/4ed737067802450a95ebde7aa43658b145069aeb))
* **hermes:** verify every pinned-source patch, not just the goal-mode ones ([#253](https://github.com/dryvist/ansible-proxmox-ai/issues/253)) ([d7fe007](https://github.com/dryvist/ansible-proxmox-ai/commit/d7fe007839f57227eccece3182950477550612c6))
* **llm_router:** point every consumer alias at a model the host will serve ([#233](https://github.com/dryvist/ansible-proxmox-ai/issues/233)) ([e5f30f9](https://github.com/dryvist/ansible-proxmox-ai/commit/e5f30f971a7fd7602771beffb1d5598a1ab4ddec))
* **squid:** allow agy's eligibility-check host ([#258](https://github.com/dryvist/ansible-proxmox-ai/issues/258)) ([f9de523](https://github.com/dryvist/ansible-proxmox-ai/commit/f9de523d9055bd983e7e26ba699daf01448ef923))
* **squid:** allow cdn.cribl.io — the transcript-shipping Edge downloads from it ([#239](https://github.com/dryvist/ansible-proxmox-ai/issues/239)) ([eaf34c2](https://github.com/dryvist/ansible-proxmox-ai/commit/eaf34c20706befe4f70984076d9f91825fd6f7ae))
* **squid:** allow platform.claude.com — the claude OAuth refresh endpoint ([#243](https://github.com/dryvist/ansible-proxmox-ai/issues/243)) ([bd90c58](https://github.com/dryvist/ansible-proxmox-ai/commit/bd90c582f5881f051b8134dc08cd0215082ea596))
* **squid:** allow the githubusercontent asset CDN by wildcard ([#232](https://github.com/dryvist/ansible-proxmox-ai/issues/232)) ([e5f524f](https://github.com/dryvist/ansible-proxmox-ai/commit/e5f524f44d5c06a9dfb699f6422b84e812997f8c))

## [0.18.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.17.0...v0.18.0) (2026-07-27)


### Features

* **hermes_agent:** enable the Vikunja bridge on this estate ([#220](https://github.com/dryvist/ansible-proxmox-ai/issues/220)) ([ba5b6d9](https://github.com/dryvist/ansible-proxmox-ai/commit/ba5b6d9e8a4a5fc3b1a265fd9f4d09be984ad400))
* **hermes_agent:** Vikunja write bridge + regenerated cron-fleet docs ([#218](https://github.com/dryvist/ansible-proxmox-ai/issues/218)) ([65ee7d5](https://github.com/dryvist/ansible-proxmox-ai/commit/65ee7d577bdc7d3ae7df0b00a0221287b325a451))


### Bug Fixes

* **hermes_agent,codex_runner:** stop apt cache refresh gating converge ([#221](https://github.com/dryvist/ansible-proxmox-ai/issues/221)) ([45c7ef4](https://github.com/dryvist/ansible-proxmox-ai/commit/45c7ef476d3fd187624979ad28a98210a5fc87a2))
* **hermes_agent:** gate splunk-status-digest's quiet posts behind a heartbeat ([#215](https://github.com/dryvist/ansible-proxmox-ai/issues/215)) ([7314d16](https://github.com/dryvist/ansible-proxmox-ai/commit/7314d16f4533d782cb222991c1f63b98bb8c778a))
* **hermes_agent:** pin the prompt catalog to the operator-summary fabrication fix ([#213](https://github.com/dryvist/ansible-proxmox-ai/issues/213)) ([af00aa9](https://github.com/dryvist/ansible-proxmox-ai/commit/af00aa92ebd38191d6c5a285ed968384f76defa8))
* **hermes_agent:** run the Splunk status digest on waking hours only ([#217](https://github.com/dryvist/ansible-proxmox-ai/issues/217)) ([efcaa5d](https://github.com/dryvist/ansible-proxmox-ai/commit/efcaa5d0cc2e809e086697654bd09ec1020997f7))
* **ollama:** derive num_parallel from the single concurrency source ([#212](https://github.com/dryvist/ansible-proxmox-ai/issues/212)) ([513cc4c](https://github.com/dryvist/ansible-proxmox-ai/commit/513cc4c1281ea47a96ff3a1da883db6fec2200f2))

## [0.17.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.16.0...v0.17.0) (2026-07-25)


### Features

* **hermes_agent:** add splunk-admin and homelab-admin operating profiles ([#203](https://github.com/dryvist/ansible-proxmox-ai/issues/203)) ([8ddec81](https://github.com/dryvist/ansible-proxmox-ai/commit/8ddec8195273c6b1d37cd702387018d271637764))
* **hermes_agent:** master Kanban digest, full-report cards, retire the 6-hourly fabric cron ([#202](https://github.com/dryvist/ansible-proxmox-ai/issues/202)) ([a2b4d75](https://github.com/dryvist/ansible-proxmox-ai/commit/a2b4d75986bd5858f479d4d0edffd16a0d7b29da))


### Bug Fixes

* **hermes_agent:** watchdog probes the router alias, not a physical id ([#197](https://github.com/dryvist/ansible-proxmox-ai/issues/197)) ([d9c4f72](https://github.com/dryvist/ansible-proxmox-ai/commit/d9c4f729e2d76ac8e4985b820057fab5a751318d))

## [0.16.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.15.0...v0.16.0) (2026-07-25)


### Features

* **hermes_agent:** escalate to host and sourcetype rollups before going quiet ([#192](https://github.com/dryvist/ansible-proxmox-ai/issues/192)) ([6157b9c](https://github.com/dryvist/ansible-proxmox-ai/commit/6157b9c7280507d287e055cd21851e676f49ec18))
* **hermes_agent:** hourly Splunk digest carries real per-run deltas ([#177](https://github.com/dryvist/ansible-proxmox-ai/issues/177)) ([c7f923b](https://github.com/dryvist/ansible-proxmox-ai/commit/c7f923bb4d896e6cf1e786edd7b2517d93e1089e))
* **hermes_agent:** operator Kanban board over the agent task store ([#179](https://github.com/dryvist/ansible-proxmox-ai/issues/179)) ([c690c13](https://github.com/dryvist/ansible-proxmox-ai/commit/c690c1399b325e2e4bafc3ed9730b3ce5b2b02b4))
* **hermes_agent:** per-day novelty gate so every digest run reports something new ([#183](https://github.com/dryvist/ansible-proxmox-ai/issues/183)) ([39a962c](https://github.com/dryvist/ansible-proxmox-ai/commit/39a962ccbd5d4dabe10fbb2bf4a7b44677beb7da))
* **hermes_agent:** retire the agentic anomaly hunt as duplicative ([#190](https://github.com/dryvist/ansible-proxmox-ai/issues/190)) ([60ced86](https://github.com/dryvist/ansible-proxmox-ai/commit/60ced86315070e9992bfe01491e2c65a50b46a32))
* **hermes_agent:** script-fed security lens; triage digests are config now ([#188](https://github.com/dryvist/ansible-proxmox-ai/issues/188)) ([98fefdf](https://github.com/dryvist/ansible-proxmox-ai/commit/98fefdfb92d0ac27e08152f4a2846599d345c122))


### Bug Fixes

* **fabric_watchdog:** pin alert channel instead of falling back to a DM ([#176](https://github.com/dryvist/ansible-proxmox-ai/issues/176)) ([fa4ac0e](https://github.com/dryvist/ansible-proxmox-ai/commit/fa4ac0ea41c9f2a749b0fa85df346f8db154f3d0))
* **hermes_agent:** never deliver tool-call markup; script-fed error triage ([#186](https://github.com/dryvist/ansible-proxmox-ai/issues/186)) ([081f4eb](https://github.com/dryvist/ansible-proxmox-ai/commit/081f4ebe836665f027695f337bdea62f507a3144))
* **hermes_agent:** pin prompts to the error-triage tool-call-leak fix ([#174](https://github.com/dryvist/ansible-proxmox-ai/issues/174)) ([ae43e17](https://github.com/dryvist/ansible-proxmox-ai/commit/ae43e1729ab7b7236ff9670edb7ba348ab4a43ff))
* **hermes_agent:** pin prompts to the two-more-digests leak fix ([#178](https://github.com/dryvist/ansible-proxmox-ai/issues/178)) ([2da36f9](https://github.com/dryvist/ansible-proxmox-ai/commit/2da36f90ecaf76fdb840be95352123932c001599))
* **hermes_agent:** stop watching superseded index `network` ([#191](https://github.com/dryvist/ansible-proxmox-ai/issues/191)) ([ba86b22](https://github.com/dryvist/ansible-proxmox-ai/commit/ba86b22ed40bde0120b42cd1dbb2b8c6e35c5ad6))
* **hermes:** pause the v2 agentic crons during a cluster window ([#198](https://github.com/dryvist/ansible-proxmox-ai/issues/198)) ([02ea533](https://github.com/dryvist/ansible-proxmox-ai/commit/02ea533716f467699d2faa303fde3319b134a39a))
* **llm_router:** absorb serving-tier 429s instead of failing the caller ([#175](https://github.com/dryvist/ansible-proxmox-ai/issues/175)) ([8b1b62a](https://github.com/dryvist/ansible-proxmox-ai/commit/8b1b62aefe709f54c9f63287be2feca9c415d094))
* **llm_router:** roll pool restarts one member at a time ([#185](https://github.com/dryvist/ansible-proxmox-ai/issues/185)) ([2cda54c](https://github.com/dryvist/ansible-proxmox-ai/commit/2cda54c6b06b9ca1a7bcbfafb1219722b0c6232c))

## [0.15.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.14.0...v0.15.0) (2026-07-24)


### Features

* **ai_runner:** add headless AI job-runner role ([#160](https://github.com/dryvist/ansible-proxmox-ai/issues/160)) ([5d13e42](https://github.com/dryvist/ansible-proxmox-ai/commit/5d13e42be110a39bd2b103fd0084d421df1dd7d6))
* **fabric_watchdog:** minutes-level MCP-fabric + LLM outage detection with Slack transition alerts ([#168](https://github.com/dryvist/ansible-proxmox-ai/issues/168)) ([c26adef](https://github.com/dryvist/ansible-proxmox-ai/commit/c26adef48f9fe587194852690b338900d2972c27))
* **hermes_agent:** declarative agentic direct-deliver digest crons ([#169](https://github.com/dryvist/ansible-proxmox-ai/issues/169)) ([0601cbc](https://github.com/dryvist/ansible-proxmox-ai/commit/0601cbc51a06961ae34b3a7b7fa4443c35e66965))
* **hermes_agent:** script-fed Splunk digest (no LLM in the fact path) ([#167](https://github.com/dryvist/ansible-proxmox-ai/issues/167)) ([86d6a33](https://github.com/dryvist/ansible-proxmox-ai/commit/86d6a334429e846a185a159e119360e9846defbc))


### Bug Fixes

* **hermes_agent:** make live budget + enqueuer-throttle patches declarative ([#162](https://github.com/dryvist/ansible-proxmox-ai/issues/162)) ([ea8a4ce](https://github.com/dryvist/ansible-proxmox-ai/commit/ea8a4cebed37da36f8953bada8047ca60cde4dbb))
* **hermes_agent:** manage zammad-auto-close from .env, drop orphan scripts ([#164](https://github.com/dryvist/ansible-proxmox-ai/issues/164)) ([ff30608](https://github.com/dryvist/ansible-proxmox-ai/commit/ff306085d91e4e65a94576792ad912e8318c034a))
* **hermes_agent:** worker-call timeout, spawn-race clarity, judge evidence contract ([#166](https://github.com/dryvist/ansible-proxmox-ai/issues/166)) ([3b0c332](https://github.com/dryvist/ansible-proxmox-ai/commit/3b0c332623d5d2ee2fd283f9ab265cf6d5a7911b))
* **hermes:** default vikunja MCP pointer off until its gateway route is live ([#161](https://github.com/dryvist/ansible-proxmox-ai/issues/161)) ([64bfb4f](https://github.com/dryvist/ansible-proxmox-ai/commit/64bfb4fb2c33dd95d63bda2c043d318f6fe5516d))

## [0.14.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.13.1...v0.14.0) (2026-07-23)


### Features

* **agentgateway:** wire read-only Vikunja MCP at the gateway; Hermes pointer ([#157](https://github.com/dryvist/ansible-proxmox-ai/issues/157)) ([bec499e](https://github.com/dryvist/ansible-proxmox-ai/commit/bec499ed72254b74fe155e8479bef679d1387522))
* **roles:** add harbor AI evaluation framework to agent and LLM container roles ([#153](https://github.com/dryvist/ansible-proxmox-ai/issues/153)) ([a7ea6c2](https://github.com/dryvist/ansible-proxmox-ai/commit/a7ea6c27150b63a940e1db19dda36873879cd2eb))


### Bug Fixes

* **hermes:** normalize deployed metric patch ([#149](https://github.com/dryvist/ansible-proxmox-ai/issues/149)) ([87d9119](https://github.com/dryvist/ansible-proxmox-ai/commit/87d9119778ecd3169f9f01f6b4daca61db641aae))
* **hermes:** preserve token metric indentation ([#146](https://github.com/dryvist/ansible-proxmox-ai/issues/146)) ([16761a7](https://github.com/dryvist/ansible-proxmox-ai/commit/16761a75cdcbf42010dce2f92d6a12f9803c3061))
* **hermes:** recover wedged scheduler queue ([#139](https://github.com/dryvist/ansible-proxmox-ai/issues/139)) ([58a2c2d](https://github.com/dryvist/ansible-proxmox-ai/commit/58a2c2d61c1d554003c8eaf68fe30b680b7a8070))
* **hermes:** retain prompt catalog build ([#150](https://github.com/dryvist/ansible-proxmox-ai/issues/150)) ([eb8f1c1](https://github.com/dryvist/ansible-proxmox-ai/commit/eb8f1c1728224108a679eca5f4650e43602fa5fd))
* **hermes:** route inference through 9b alias ([c371b73](https://github.com/dryvist/ansible-proxmox-ai/commit/c371b7319a6f273bfb828341b79c5707a3112fa4))
* **hermes:** run Kanban workers through goal loop ([#141](https://github.com/dryvist/ansible-proxmox-ai/issues/141)) ([b973b62](https://github.com/dryvist/ansible-proxmox-ai/commit/b973b62a248a569f0dab6f0ff82502137b834a8f))
* **hermes:** use proven model for goal judging ([#142](https://github.com/dryvist/ansible-proxmox-ai/issues/142)) ([db78dd0](https://github.com/dryvist/ansible-proxmox-ai/commit/db78dd0e7c711a11521a5801e7cb2ba3dbc8ac56))
* **hermes:** use runtime goal judge and retry model calls once ([#144](https://github.com/dryvist/ansible-proxmox-ai/issues/144)) ([7997c9f](https://github.com/dryvist/ansible-proxmox-ai/commit/7997c9fe02f2e4138292049a2d16027de0dd75b1))
* **hermes:** use runtime-selected fast goal judge ([#143](https://github.com/dryvist/ansible-proxmox-ai/issues/143)) ([aac89f3](https://github.com/dryvist/ansible-proxmox-ai/commit/aac89f3e17e34b6b51ba31ca09a913b9c7ba01cc))

## [0.13.1](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.13.0...v0.13.1) (2026-07-22)


### Bug Fixes

* **openbao:** seed Hermes API key atomically ([bc1aa69](https://github.com/dryvist/ansible-proxmox-ai/commit/bc1aa6921aa2a601fec47dba408fa3e21030414c))
* **openbao:** seed Hermes API key atomically ([f78f11b](https://github.com/dryvist/ansible-proxmox-ai/commit/f78f11bdc1427176590f0d852dcdbe98da03da1b))

## [0.13.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.12.0...v0.13.0) (2026-07-22)


### Features

* **hermes_agent:** enforce kanban finalization via goal-mode loop ([9dd15c5](https://github.com/dryvist/ansible-proxmox-ai/commit/9dd15c55705c02cf477e62d58d0eb1f879351039))


### Bug Fixes

* **hermes_agent:** clamp compression traversal bounds ([f9f6015](https://github.com/dryvist/ansible-proxmox-ai/commit/f9f6015d48af3edde83cb28c4d01d943dbbdd7a2))
* **hermes_agent:** reconcile recurring goal mode ([#132](https://github.com/dryvist/ansible-proxmox-ai/issues/132)) ([6315f1a](https://github.com/dryvist/ansible-proxmox-ai/commit/6315f1a9abac8a1dde223b5d35f0e3b091b83765))

## [0.12.0](https://github.com/dryvist/ansible-proxmox-ai/compare/v0.11.0...v0.12.0) (2026-07-21)


### Features

* **hermes_agent:** wire bot-pr-triage and docs-sync Kanban jobs ([a445839](https://github.com/dryvist/ansible-proxmox-ai/commit/a4458392357887feaea37bfd854ccb040c95e1e6))
* **hermes:** add authenticated dashboard service ([#127](https://github.com/dryvist/ansible-proxmox-ai/issues/127)) ([0e65eb2](https://github.com/dryvist/ansible-proxmox-ai/commit/0e65eb2654dcc0121580091957c5fe111f519af3))

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
