# langfuse_docker

Deploys the Langfuse LLM-observability stack as a Docker Compose project in an
LXC: a relational database, ClickHouse, Redis, and the web and worker services.

## Installation

This role ships in the `ansible-proxmox-ai` repository and is applied via
`playbooks/site.yml`. No separate installation is required beyond cloning the
repo and installing collection dependencies:

```bash
git clone https://github.com/dryvist/ansible-proxmox-ai.git
cd ansible-proxmox-ai
ansible-galaxy collection install -r requirements.yml
```

## Prerequisites

- An LXC tagged for this app with Docker enabled, and its data directory backed
  by the persistent volume the deployment manifest declares for it.
- The object-storage endpoint reachable — event and media uploads already go
  there rather than to a bundled blob service.
- The secret store reachable, or the corresponding env fallbacks present. The
  three values below are what the role refuses to proceed without.

## Usage

```bash
sops exec-env secrets.enc.yaml 'doppler run -- ansible-playbook playbooks/site.yml --tags langfuse'
```

## The three application secrets

`ENCRYPTION_KEY`, `SALT` and `NEXTAUTH_SECRET` must stay stable for the life of
the data. `ENCRYPTION_KEY` protects the provider credentials this app stores in
its relational database.

They were generated into the app's own data directory — the same directory as
the database they protect — so a loss of that directory took the data **and**
the means to read it in one step, and a restore against fresh values yields a
database that loads with its stored credentials permanently unreadable. Nothing
errors when that happens.

The role now resolves each from the secret store first, then the guest's file,
and **asserts all three non-empty before deploying**. It never generates a
replacement for an install that already has one.

## What in this stack needs protecting

| Component | Needs protection | Why |
| --- | --- | --- |
| Relational database | **Yes** | Orgs, projects, users, prompts, datasets, and encrypted provider credentials. Not reconstructible. |
| ClickHouse | **Durability, not DR** | Observability history. Losing it loses retrospect, not function — see below. |
| Redis | **No** | Cache and queue state, ephemeral by design. |
| Blob storage | **No** | Already written to object storage, not to this guest. |

ClickHouse is deliberately not proposed for backup. It holds telemetry, the same
class the estate already declines to back up for its log and metric stores, and
it is the largest thing here by a wide margin. What it needs is to be **on the
persistent volume** rather than in a container's writable layer — durability, so
an ordinary container lifecycle event does not take it. That is a placement
question, not a backup one.

## Storage: the declared design is already right

The deployment manifest declares a persistent volume for this app's data
directory, and this role bind-mounts the relational, ClickHouse and Redis data
directories beneath it. Nothing here needs redesigning.

If a guest is observed keeping that data inside the container storage instead,
that is **drift from the declared state, not the declared state**. Fix it by
reconciling the guest, not by changing this role.

**The reconciliation has a trap that destroys data.** Attaching the declared
volume at the data directory of a guest that currently holds live data at that
same path **masks** the existing contents — the data is still on the old
filesystem, invisible, and the app comes up empty and initializes fresh. The
existing contents must be moved onto the new volume before it is attached at
that path, never after.

## When the container runtime cannot list its own containers

A guest can reach a state where the app's processes are running while the
container runtime reports no containers at all. That is a management-plane
failure, not an application failure, and it matters here for one specific
reason: **it changes where the data is and therefore what is safe to do.**

Determine which before planning anything, using reads only — no runtime
commands, no restarts, no recreate:

- Read the mount table of a running data process (`/proc/<pid>/mountinfo`). If
  its data directory resolves to a host path under the app's data directory,
  the data is on a bind mount, survives a container recreate, and is copyable
  with ordinary file tools.
- If instead it resolves inside the runtime's own layer storage, the data is in
  a writable layer that the runtime can no longer address, and **a recreate
  discards it** — there is no runtime command that can reach it first.

Any migration plan that assumes the containers can be stopped and their data
copied out through the runtime will fail on contact in the second case. Settle
this question first; it determines whether the move is a routine dump or a
filesystem-level rescue.

The underlying cause — a guest missing the container features its runtime
storage driver needs, which are set only at guest creation — is an
infrastructure gap tracked separately. It is not fixable from this role, and it
should be addressed the way other creation-only guest features already are:
applied from the host after creation, not by recreating a data-bearing guest.

## Moving the relational database to the shared cluster

Its contents belong on the shared PostgreSQL cluster, where they inherit that
cluster's continuous WAL archiving, point-in-time recovery and nightly logical
dumps, exactly as for the other apps moved there.

The same open question applies as for the other app in this repository: the
login role here is the superuser of its own instance, and a managed role on the
shared cluster is not. Read the extension list from a running instance
(`SELECT extname FROM pg_extension`) and declare exactly those on the managed
database rather than granting superuser. Do not guess the list.

Sequence it the same recoverable way: capture the three secrets into the store
and verify they read back; create the database on the shared cluster while the
app is untouched; stop only the app and dump; load; repoint and converge; verify
a stored provider credential still works before reclaiming anything.
