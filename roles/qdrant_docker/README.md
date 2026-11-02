# Qdrant Docker role

Deploys Qdrant as a Docker container and manages its persistent storage.

## Restoring a pre-upgrade snapshot

Full-storage snapshots are stored in `qdrant_docker_snapshots_dir` on the host
and mounted at `/qdrant/snapshots` in the container. List available snapshots
with Qdrant's `GET /snapshots` API using the configured API key.

To restore a full-storage snapshot, stop the Qdrant container, then start
Qdrant with `--storage-snapshot <path>` pointing to the snapshot file, or use
the snapshot recover API documented for the Qdrant release in use.
