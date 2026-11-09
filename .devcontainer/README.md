# Devcontainer

Everything you need to work on La Fabrique (backend, frontend and the LLM
proxy) lives inside this container: Python 3.12 + Poetry, Node 24, Docker
(docker-in-docker) and the GitHub CLI. Infra services (Postgres, NATS, Neo4j,
Jaeger) are started from inside with `docker compose up -d`.

## Pushing from inside the container

The repo's remote is SSH (`git@github.com:careb36/la-fabrique-private.git`), so
the container needs access to your GitHub credentials. Two options:

### 1. SSH agent forwarding (configured by default)

`devcontainer.json` forwards your host SSH agent into the container via
`SSH_AUTH_SOCK`. For it to work, your key must be loaded into the agent on the
host **before** you open the container:

```bash
# macOS (host), once:
ssh-add --apple-use-keychain ~/.ssh/id_ed25519   # or the path to your key

# Linux (host), once:
ssh-add ~/.ssh/id_ed25519

# Windows (host), start the OpenSSH Authentication Agent service, then:
ssh-add $env:USERPROFILE\.ssh\id_ed25519
```

> The mount reads `SSH_AUTH_SOCK` from the host environment, so the agent must
> be running and have your key loaded **before** the container is created or
> rebuilt; otherwise the socket isn't forwarded.

Then, inside the container, `git push` just works over SSH. Check the agent is
visible with:

```bash
ssh-add -l          # should list your key
ssh -T git@github.com   # should greet you as your GitHub user
```

> **Troubleshooting:** if `ssh-add -l` returns "Could not open a connection to
> your authentication agent", the socket wasn't forwarded. Rebuild the
> container (`Dev Containers: Rebuild Container`) with the agent running on the
> host, or fall back to option 2.

### 2. HTTPS with the GitHub CLI

If you don't use SSH keys, switch the remote to HTTPS and let `gh` handle the
credentials. Inside the container:

```bash
gh auth login                 # browser/device-code flow, once
gh auth setup-git             # configures git's credential helper
git remote set-url origin https://github.com/careb36/la-fabrique-private.git
```

After that, `git push` authenticates with the `gh` token. The remote change is
local-only (remotes aren't committed).

## Git identity

The `git` devcontainer feature usually picks up your host `user.name`/`user.email`.
If commits fail with "Please tell me who you are", set it once inside the container:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```
