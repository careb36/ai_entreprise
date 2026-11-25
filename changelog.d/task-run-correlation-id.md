Add a stable `correlation_id` (UUID) generated once at outbox enqueue time
and carried through publish, JetStream consume, daemon call, verification,
and `AgentRun`. It stays immutable across retries and redeliveries, distinct
from `Nats-Msg-Id`. Unit tests assert persistence and OTel span attribute
visibility; a verifier test traces publish → consume → daemon → verifier
→ `AgentRun` end to end.
