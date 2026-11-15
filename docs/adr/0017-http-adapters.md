# ADR 0017: HTTP only in named adapter modules

## Status

Accepted (2026-11-14). ADR-6 of architecture v1, *La Fabrique backend: contexts and their dependency rule*, for the specification *Backend contexts and their dependency rule*. Written by La Fabrique from the architecture record; edit it there, not here.

## Context

Service modules called HTTP libraries inline (the models.dev catalogue, a provider's model list, the notification webhook, the OpenProject tool, the research fetch, the pre-QA sandbox, the GitHub adapter, the health probes, the MCP client), so a domain decision and a transport exception lived in one function and nothing stopped the next one. Issue #511; the rule's idea is AixleHQ/flow's "no HTTP in domain services" design rule (Apache-2.0, design only).

## Decision

Under backend/app/services, only the modules listed in http_adapters (docs/architecture/contexts.json) may import an HTTP library (httpx, httpx2, requests, aiohttp, urllib3, urllib.request). An adapter sends the request and returns data, raising named errors rather than transport exceptions; the domain module calls it and decides. scripts/check_architecture.py fails on an HTTP import outside the list and reports a listed module that no longer imports one, which --update drops.

## Consequences

Five modules moved their HTTP into a new adapter beside them (integrations/webhook_client, models/provider_client, producers/openproject_client, tools/research_http, workspace/sandbox_http); four already were adapters or probes and are listed as they are (models/model_catalog, platform/healthcheck, producers/github_adapter, tools/mcp_client). The list may only shrink, unless a new adapter is reviewed in the pull request that adds it.
