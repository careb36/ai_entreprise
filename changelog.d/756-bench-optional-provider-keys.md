Fixed

* **The real-model bench requires only `DEEPSEEK_API_KEY` (issue #756).** `QWEN_API_KEY` is passed to the bench's LLM proxy only when its repository secret is set, so a missing optional key never reaches the proxy as an empty value. `bench/README.md` says which key is required.
