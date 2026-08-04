# dify_docker

Deploys the Dify LLMOps platform as a Docker Compose stack in an LXC.

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

- An LXC tagged `dify` (nesting enabled for Docker) with its data directory on
  a persistent mount.
- The secret store reachable, or the corresponding env fallbacks present. The
  key below is the one value the role refuses to proceed without.

## Usage

```bash
sops exec-env secrets.enc.yaml 'doppler run -- ansible-playbook playbooks/site.yml --tags dify_docker'
```

## SECRET_KEY

`SECRET_KEY` signs sessions and protects the material that decrypts what the
app has already stored. Two properties follow, and both are load-bearing:

- **It is part of the backup.** Data restored against a different key is
  unreadable. Nothing errors: the stack starts, the pages load, and the loss
  surfaces only when something tries to use a stored credential.
- **It must not live only beside the data it unlocks.** Its canonical home is
  the secret store; the generate-once file on the guest is the greenfield
  bootstrap and a local fallback, nothing more.

The role resolves it from the secret store first, then the guest, and **asserts
a non-empty result before deploying**. It never generates a replacement for an
install that already has one.

## What in this stack needs protecting

| Component | Needs protection | Why |
| --- | --- | --- |
| Relational database | **Yes** | Apps, datasets, and stored provider credentials. Not reconstructible. |
| App storage directory | **Yes** | Uploaded source documents plus the key material the stored credentials depend on. The other half of a usable restore. |
| Vector store | **No** | A derived index, re-embedded from the uploads and their database records — both covered above. Far larger than what it duplicates. |
| Cache | **No** | Ephemeral by design. |

## Moving the relational database to the shared cluster

The database currently runs as a service inside this stack, on a mount the
guest-level backup mechanism cannot capture. The shared PostgreSQL cluster
already carries continuous WAL archiving, point-in-time recovery, and nightly
logical dumps, and moving there inherits all of it.

**One thing must be resolved before that move, and it is not cosmetic.** In this
stack the app's login role is the superuser of its own instance, which is what
lets its migrations create whatever extensions they need. On the shared cluster
a managed login role owns its database but is not a superuser, and creating an
untrusted extension needs one. The shared cluster's role already supports
declaring a database's extensions and enabling them as the superuser — the open
question is only **which** extensions this app's migrations require.

Resolve it by reading the extension list from a running instance
(`SELECT extname FROM pg_extension`) and declaring exactly those on the managed
database, rather than granting superuser to work around it. Until that list is
known, this role deliberately still runs its own database service: an app
pointed at a database whose migrations cannot complete is worse than one whose
database is merely in the wrong place.

Once the list is declared, the cutover follows the same recoverable ordering
used for the other app moved to this cluster: capture the key into the secret
store and verify it reads back; create the databases on the shared cluster while
the app is untouched; stop only the app and dump; load; repoint and converge;
verify a stored credential actually works before reclaiming anything.
