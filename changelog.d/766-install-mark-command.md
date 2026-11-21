Fixed

* **A changed install command runs again in a task's worktree (issue #766).** The first-install mark (`<folder>/` or `./` in the company's `dependency_installs`) said only "installed", so a company that changed a folder's install command (the self-hosting profile adding llm-proxy's `requirements-dev.txt`) kept the old install, and its suite still found no pytest. The mark now holds the command's digest (`sha256:` and 16 hex, `dependency_install.command_digest`); a different command, or a mark from before, installs again on the next delivery.
