# Devcontainer

Everything you need to work on La Fabrique (backend, frontend and the LLM
proxy) lives inside this container: Python 3.12 + Poetry, Node 24, Docker
(docker-in-docker) and the GitHub CLI. Infra services (Postgres, NATS, Neo4j,
Jaeger) are started from inside with `docker compose up -d`.

## Pushing from inside the container

The repo's remote is SSH (`git@github.com:careb36/ai_entreprise.git`), so
the container needs access to your GitHub credentials. Two options:

### 1. SSH keys from the host (configured by default)

`devcontainer.json` mounts your host `~/.ssh` into the container read-only, so
the container uses the same keys and `known_hosts` you already use on the host.
Make sure your key exists on the host:

```bash
ls ~/.ssh          # you should see id_ed25519 / id_rsa and known_hosts
```

Then, inside the container, `git push` just works over SSH. Check the key is
visible with:

```bash
ssh -T git@github.com   # should greet you as your GitHub user
```

> **Why not agent forwarding?** The common `SSH_AUTH_SOCK` mount does not work
> on macOS: Docker Desktop can't bind-mount the launchd socket that macOS's
> `ssh-agent` uses (`/var/run/com.apple.launchd.*/Listeners`). Mounting `~/.ssh`
> is the reliable cross-platform approach, at the cost of giving the (read-only)
> container access to the key material.
>
> **Troubleshooting:** if the host's `known_hosts` doesn't contain `github.com`
> yet, run `ssh -T git@github.com` once on the host to accept the host key, or
> fall back to option 2.

### 2. HTTPS with the GitHub CLI

If you don't use SSH keys, switch the remote to HTTPS and let `gh` handle the
credentials. Inside the container:

```bash
gh auth login                 # browser/device-code flow, once
gh auth setup-git             # configures git's credential helper
git remote set-url origin https://github.com/careb36/ai_entreprise.git
```

After that, `git push` authenticates with the `gh` token. The remote change is
local-only (remotes aren't committed).

## Git identity

Your host `~/.gitconfig` is mounted into the container, so `user.name` and
`user.email` are available out of the box. If your host keeps its git config
elsewhere (e.g. `~/.config/git/config`) or you see "Please tell me who you
are", set it once inside the container:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```
