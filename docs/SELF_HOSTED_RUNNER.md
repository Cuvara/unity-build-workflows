# Self-Hosted Runners

> **Start here:** self-hosted runners support **two** build engines —
> `local` (Unity Hub, no Docker) and `docker` (GameCI image via Docker
> Desktop). See [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) for
> the full Runner-vs-Build-Engine model and which one to pick. This document
> covers the **`self-hosted` + `docker`** (`selfhosted-docker`) setup
> specifically. For `self-hosted` + `local` (recommended for Unity
> Personal/Free), see
> [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) instead.

Self-hosted runners (either build engine) are recommended when:

- Build times on GitHub-hosted runners are too slow
- Unity license costs require minimizing concurrent activations
- Network access to private registries is required
- Specific hardware (large memory, fast storage) is needed

Set the repo variables for this lane:

```
RUNNER_TYPE=self-hosted
BUILD_ENGINE=docker
RUNNER_LABELS=self-hosted,windows   # match your registered runner's labels
```

---

## Requirements (docker build engine)

The self-hosted host must have **Docker Engine** installed and running. This
section covers the `docker` build engine only — the `local` build engine
(Unity Hub) has no Docker requirement; see
[SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md).

| Component | Requirement |
|---|---|
| OS | Ubuntu 22.04+ (recommended), or Windows/macOS with Docker Desktop |
| Docker Engine | 20.10+ with BuildKit support |
| RAM | 16 GB minimum (32 GB recommended for IL2CPP) |
| Storage | 100 GB SSD (Docker images + cache volumes) |
| GPU | Not required for builds |

### Docker Installation

```bash
# Install Docker Engine
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add runner user to docker group
sudo usermod -aG docker $USER

# Verify
docker run --rm hello-world
```

---

## Registering the Runner

### Organization-Level (recommended)

1. **Organization Settings → Actions → Runners → New self-hosted runner**
2. Select your OS
3. Follow the setup script
4. Add labels matching your intended `RUNNER_LABELS` value, e.g.
   `self-hosted`, `docker`

### Repository-Level

1. **Repository Settings → Actions → Runners → New self-hosted runner**
2. Follow the setup script
3. Add labels matching your intended `RUNNER_LABELS` value

> **Labels must match exactly** (after comma-split/trim/dedup) between the
> runner's registration and the `RUNNER_LABELS` repo variable, or the job
> queues indefinitely with "no runner matching labels found."

---

## Runner as a Service

```bash
# After ./config.sh
sudo ./svc.sh install
sudo ./svc.sh start
sudo systemctl enable actions.runner.<org>-<repo>.<runner-name>
```

---

## Docker Image Caching

Pre-pull Unity images on the runner to avoid download time during builds:

```bash
docker pull ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0
docker pull ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-webgl-v2.0.0
docker pull ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-linux-v2.0.0
```

Set up a cron job to pull latest images weekly.

---

## Library Cache Volumes

Docker named volumes persist Unity Library caches between builds:

```bash
# List cache volumes
docker volume ls | grep unity-lib

# Remove a corrupted cache
docker volume rm unity-lib-<hash>

# Remove all Unity cache volumes
docker volume ls -q | grep unity-lib | xargs docker volume rm
```

---

## Security Hardening

1. **Run as a dedicated low-privilege user** — not root
2. **Docker group access only** — runner user in `docker` group
3. **No Docker socket mount** — Unity containers never access the Docker socket
4. **Outbound firewall** — allow only required endpoints (GitHub, Unity licensing, your registry)
5. **Separate runners by environment** — staging and production use different runner groups
6. **Regular updates** — keep Docker Engine and runner agent updated

---

## Storage Management

Docker images and volumes consume significant storage:

```bash
# Check Docker disk usage
docker system df

# Clean unused images (keeps pulled images)
docker image prune -f

# Clean all unused resources (aggressive)
docker system prune -f
```

Set up a weekly cleanup job:
```bash
# Crontab entry
0 3 * * 0 docker system prune -f --volumes --filter "until=168h"
```

---

## Troubleshooting

**"No runner matching labels found"** — Check the runner's registered labels
match `RUNNER_LABELS` exactly (after normalization).

**More than one build machine?** Register each one with a label unique to it (its
name) plus what it can build (e.g. `unity-docker`). Then describe them in a
runner policy, and each Unity job is sent to an eligible, available machine in
the order you choose, with an explicit fallback. See
[MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md).

**"Cannot connect to Docker daemon"** — Ensure Docker is running and the
runner user is in the `docker` group.

**"Permission denied"** — Verify Docker group membership: `groups $USER | grep docker`

**"Disk full"** — Run `docker system prune -f` and check volume usage.

---

## See also

- [MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md) — several runners:
  priority, fallback, pools, groups, availability.
- [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) — Runner vs Build
  Engine architecture, all three execution strategies, licensing per mode.
- [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md#runner) — `RUNNER_TYPE` /
  `BUILD_ENGINE` / `RUNNER_LABELS` variable reference and migration table.
- [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) — resolver contract.
- [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) — the
  `self-hosted` + `local` (no Docker) setup, recommended for Unity
  Personal/Free.
