# TCCS Core container deployment

TCCS Core is deployed separately from the existing controller database. The Compose stack keeps the existing `tccs` database intact and creates a dedicated `tccs_core` database for Core's Alembic-managed schema.

## Runtime

- Core image: Python 3.12 slim
- Core HTTP API: port 8080
- PostgreSQL: existing `postgres` service on port 5432
- Asterisk ARI: host Asterisk HTTP API on port 8088
- ARI application: `tccs-core`

The Core container uses `host.docker.internal` to reach Asterisk on the Docker host. Do not change the existing Asterisk PJSIP configuration as part of this deployment.

## Configuration

Copy `deployment/core.env.example` to an untracked `.env` file at the repository root and replace `TCCS_ASTERISK_ARI_PASSWORD` with the password configured for the `[tccs]` ARI user. Keep credentials out of Git.

The Core database URL must point to `tccs_core`, not the existing controller database `tccs`.

## Start

```bash
docker compose build core
docker compose up -d postgres core-db-init core
```

`core-db-init` creates `tccs_core` if it does not already exist. The Core container then runs `alembic upgrade head` before starting the application.

## Verify

```bash
docker compose ps
curl -fsS http://127.0.0.1:8080/api/v1/health/ready
asterisk -rx "ari show apps"
```

The ARI application list should contain `tccs-core` after the Core event WebSocket connects.

## Safety

Do not run Core migrations against the existing controller database. The repository's Core migrations define a separate schema. Do not copy the repository's generic Asterisk configuration over the controller-generated files under `/etc/asterisk`.
