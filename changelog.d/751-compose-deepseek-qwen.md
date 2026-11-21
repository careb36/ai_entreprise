Removed

* **The deployment's configuration names only DeepSeek and Qwen (issue #751).** `docker-compose.yml` no longer forwards `GEMINI_API_KEY`, `NEBIUS_API_KEY`, `NEBIUS_PROXY_URL` or the `NEMOTRON_*` variables, so a key exported in the shell for another tool never reaches the proxy or the backend; `.env.example` lists only `DEEPSEEK_API_KEY` and `QWEN_API_KEY`. The code that could serve other providers is unchanged.
