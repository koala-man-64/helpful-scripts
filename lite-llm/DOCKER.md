# Local Docker

Run these commands from this folder in PowerShell. Docker Desktop must use Linux
containers. The Compose project is `litellm-local`; only
`127.0.0.1:4000` is published, so Claude Code continues using
`http://localhost:4000` without a client URL change.

## Configuration

Use your existing working LiteLLM configuration as the local `config.yaml`.
`config.docker.example.yaml` shows a complete static-key configuration for the
two existing aliases. Preserve your working Foundry deployment and authentication
parameters. Add `general_settings.master_key: os.environ/LITELLM_MASTER_KEY` if
the working file does not already select your proxy key.

Place the existing proxy key and only the provider variables referenced by that
configuration in `.env`; `.env.example` shows the key-based variant. Values use
raw `KEY=value` syntax without surrounding quotes. The proxy key must match
Claude Code's `ANTHROPIC_AUTH_TOKEN`. Both files are ignored by Git. Never run
`docker compose config` without `--quiet` on a populated setup: rendered output
can contain secrets.

Host Azure CLI sign-in is not automatically available inside a container. If
your existing configuration uses Azure AD, use its existing container-compatible
credential mechanism; do not substitute a dummy key or mount the whole home
directory. A proxy that starts with an empty model list has no inference routes.

## Start and verify

```powershell
docker compose config --quiet
docker compose up -d --wait --wait-timeout 120
docker compose ps
curl.exe --fail --silent --show-error http://localhost:4000/health/liveliness
curl.exe --fail --silent --show-error http://localhost:4000/health/readiness
```

The healthcheck uses liveliness and does not spend inference tokens. Healthy
means the proxy process is available, not that Foundry credentials work.

After the Foundry aliases and matching Claude Code key are configured, run:

```powershell
'sonnet', 'opus' | ForEach-Object {
    claude --model $_ -p "Do not use tools. Reply exactly ROUTE_OK."
    if ($LASTEXITCODE -ne 0) { throw "Routing test failed for $_" }
}
docker compose logs --tail 50 litellm
```

Expect two successful replies and corresponding proxy requests. Use the
[routing guide](README.md) to establish the outbound Foundry hop; access logs
alone do not prove which provider was called. Do not share unredacted logs or
enable detailed debugging for normal use.

## Stop, restart, and remove

```powershell
docker compose stop
docker compose start
# Apply edited config/env values by recreating the container:
docker compose up -d --force-recreate --wait --wait-timeout 120
# Remove only this Compose project's container and network; retain local files:
docker compose down
```

The image is pinned for reproducibility. To roll back an image change, restore
the previously verified image reference in `compose.yaml` and run `up` again.
If this worktree will be removed, first move the deployment files and ignored
configuration to a permanent directory and recreate the Compose service there;
the running container depends on the bind-mounted `config.yaml` in this folder.

## Ownership and scope

Rudy owns this local environment, its credentials, and retirement. Compose owns
container/network lifecycle. The serving process runs as the official image's
non-root user, has all Linux capabilities dropped, and cannot acquire new
privileges. Its configuration mount is read-only; it receives no Docker socket
or host Azure management credentials. It may call the explicitly configured
Foundry inference endpoints but does not provision or modify Azure resources.

This is a single-process API gateway. No Postgres or Redis is added, no virtual
keys or persistent spend budgets are enabled, and no model-pool or CLI-backend
behavior is introduced. Log files rotate at 10 MB with three files retained.

Sources: [official database-free deployment](https://docs.litellm.ai/docs/proxy/docker_quick_start#running-without-a-database)
and [official image guidance](https://docs.litellm.ai/docs/proxy/docker_image_security).
