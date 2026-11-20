Fixed

* **`scripts/land.sh` checks the frontend again (issue #766).** The checking worktree lived under `.git/fabrique/worktree`, and Vite refuses to serve files under `**/.git/**`: vitest there could not load its setup file, 156 suites ran no test and the header test got a 403, so every landing failed on "frontend tests". The default is now beside the checkout, `<checkout>.land-worktree`; `LAND_WORKTREE` still names another.
