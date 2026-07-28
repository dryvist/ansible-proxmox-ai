# squid

Converges the `squid` LXC into the AI agent plane's **egress chokepoint**: a
minimal forward proxy whose per-profile domain allowlist is the only thing
standing between a permission-skipping autonomous agent and the internet.

## Why this role is the security boundary

A guest on the `ai-proxied` Proxmox firewall profile has **no direct WAN rule at
all**. Its entire permitted egress is DNS (53), NTP (123), the OpenBao API
(8200), the Cribl log-ingest frontends, and TCP `squid_proxy` to this guest —
see [`modules/firewall/ai_proxied_rules.tf`][fw] in `tofu-proxmox`. So CONNECT
through here is the one path off the estate.

The firewall stops at L3/L4: it cannot express "this guest may reach
`api.anthropic.com` but not `example.com`". That distinction is the whole point
of the profile, which is why the hostname allowlist lives in this role. Per-CLI
variants are *not* separate firewall profiles — they are ACL groups here.

[fw]: https://github.com/dryvist/tofu-proxmox/blob/develop/modules/firewall/ai_proxied_rules.tf

## Installation

Applied by `playbooks/site.yml` to `squid_group` — the group
`inventory/load_tofu.yml` builds from the tofu **`squid`** tag. The play runs
**before** the `ai_agent_pool_group` play by construction: a pool guest cannot
install a single package until this proxy answers.

```bash
doppler run -- ansible-playbook -i inventory/hosts.yml playbooks/site.yml \
  --tags squid --limit squid_group,localhost
```

## Usage

Nothing calls this proxy explicitly. The `agent_guest` role points each pool
guest at it — `http_proxy`/`https_proxy` in apt, npm, the login shell, systemd's
default environment and every `agent-task@` job unit — so an agent's CLI, `git`,
`gh` and `curl` all traverse it without knowing it exists. The proxy's own
coordinates are inventory-derived there: the FQDN from this `squid_group`, the
port from `tofu_data.constants.service_ports.squid_proxy`.

The operational surface is therefore the allowlist below and the access log.

## Egress profiles

`squid_egress_profiles` maps a firewall egress profile to an inventory group and
a domain allowlist. The template turns each entry into one ACL pair:

```squid
acl ai_proxied_clients src <every member's DHCP reservation>
acl ai_proxied_domains dstdomain <the allowlist>
http_access allow ai_proxied_clients ai_proxied_domains
```

followed by a single `http_access deny all`. Two consequences worth knowing:

- **Client addresses are never written here.** They come from
  `container_reserved_ip` on each member of the profile's inventory group, so
  adding a pool guest is a tofu change and a re-publish, not an edit to this
  role.
- **A profile with no members emits no rule**, and its clients fall through to
  the deny. An empty group is a silent no-egress, not a silent allow.

Adding a profile is a data change in `defaults/main.yml`: name it, point it at
its inventory group, list its domains.

### The `ai_proxied` allowlist

| Purpose | Hosts |
| --- | --- |
| Debian packages | `deb.debian.org`, `security.debian.org` |
| GitHub (clone, push, PR) | `github.com`, `api.github.com`, `codeload.github.com` |
| GitHub assets (release tarballs, `gh` apt channel) | `objects.githubusercontent.com`, `raw.githubusercontent.com`, `uploads.github.com`, `cli.github.com` |
| npm (the three agent CLIs) | `registry.npmjs.org` |
| Anthropic (`claude`) | `api.anthropic.com`, `statsig.anthropic.com` |
| OpenAI (`codex`) | `api.openai.com`, `auth.openai.com`, `chatgpt.com` |
| Google (`agy`) | `generativelanguage.googleapis.com`, `cloudcode-pa.googleapis.com`, `oauth2.googleapis.com`, `accounts.google.com` |

**Widening this list is a security decision.** Every entry is a host an
autonomous agent running in permission-skipping mode may reach; the LXC and the
firewall give it nothing else. Treat an addition the way you would treat a new
firewall rule, not the way you would treat a config tweak.

## Configuration

| Variable | Source | Purpose |
| --- | --- | --- |
| `squid_port` | `tofu_data.constants.service_ports.squid_proxy` | Listen port (no hardcode) |
| `squid_egress_profiles` | role default | Profile → inventory group + allowlist |
| `squid_safe_ports` / `squid_ssl_ports` | role default | IANA protocol constants; confine CONNECT to TLS |
| `squid_config_path` | role default | Rendered config destination |

`squid_safe_ports` and `squid_ssl_ports` are the one place a port literal
appears in this role. They are protocol constants ("the port HTTPS speaks"),
not estate service ports, and there is no tofu constant for them — naming them
here keeps them out of the template. Confining CONNECT to TLS ports stops an
allowlisted hostname from being used to tunnel to some other service on that
same host.

## What it deliberately does not do

- **No `cache_dir`.** Squid caches in memory only, so nothing an agent fetched
  survives a restart and there is no disk cache to reason about.
- **No stock `squid.conf`.** The rendered config is only rules and the values
  they need. A wholesale copy of Debian's default would bury the allowlist in
  several hundred lines of commented-out examples.
- **No client identity beyond the source address.** Proxy auth would mean a
  credential on every pool guest for no gain — the guests are already the only
  hosts the firewall lets in on this port, scoped to the ai VLAN.

`forwarded_for delete` and `via off` keep the internal client address out of
requests to the external services above.

## Verification

The config is validated by squid's own parser before it is installed
(`validate: squid -k parse -f %s`), so a broken render fails the task rather
than taking the plane's egress down on the restart that follows.

To check the boundary from a pool guest after a converge:

```bash
# allowed
curl -sS -o /dev/null -w '%{http_code}\n' -x "$https_proxy" https://api.anthropic.com/
# denied — expect 403 from squid, not a connection to the origin
curl -sS -o /dev/null -w '%{http_code}\n' -x "$https_proxy" https://example.com/
```

Denials are logged as `TCP_DENIED` in `/var/log/squid/access.log` on this guest;
that log is the first place to look when an agent job fails on a network error.
