Fixed

* **The self-hosting profile installs the LLM proxy's test dependencies (issue #766).** `scripts/self_host/self_host.py` installed `llm-proxy/` with Poetry only, but its pytest comes from `llm-proxy/requirements-dev.txt`, as in CI: in the sandbox its suite failed with "No module named pytest". The folder's install now adds `poetry run pip install -r requirements-dev.txt`.
