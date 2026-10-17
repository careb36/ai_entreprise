import math
import time
from collections import OrderedDict

from starlette.responses import JSONResponse


class SecurityMiddleware:
    def __init__(
        self,
        app,
        max_requests: int = 120,
        window_seconds: int = 60,
        max_clients: int = 10_000,
    ):
        if max_requests < 1 or window_seconds < 1 or max_clients < 1:
            raise ValueError("Rate limit settings must be positive")
        self.app = app
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._requests: OrderedDict[str, tuple[int, int]] = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower()
                    not in {
                        b"x-content-type-options",
                        b"x-frame-options",
                        b"referrer-policy",
                        b"permissions-policy",
                        b"content-security-policy",
                    }
                ]
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                        (
                            b"permissions-policy",
                            b"camera=(), microphone=(), geolocation=()",
                        ),
                    ]
                )
                if scope["path"].startswith("/v1/"):
                    headers.append(
                        (
                            b"content-security-policy",
                            b"default-src 'none'; frame-ancestors 'none'",
                        )
                    )
                if scope.get("scheme") == "https":
                    headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message["headers"] = headers
            await send(message)

        if scope["path"].startswith("/v1/") and scope["method"] != "OPTIONS":
            now = time.monotonic()
            window = int(now // self.window_seconds)
            client = (scope.get("client") or ("unknown", 0))[0]
            current_window, count = self._requests.get(client, (window, 0))

            if current_window == window and count >= self.max_requests:
                retry_after = max(
                    1, math.ceil((window + 1) * self.window_seconds - now)
                )
                response = JSONResponse(
                    {"detail": "Rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                return await response(scope, receive, send_with_security_headers)

            if len(self._requests) >= self.max_clients:
                for key, (seen_window, _) in list(self._requests.items()):
                    if seen_window != window:
                        del self._requests[key]
                while len(self._requests) >= self.max_clients:
                    self._requests.popitem(last=False)

            self._requests[client] = (
                window,
                count + 1 if current_window == window else 1,
            )
            self._requests.move_to_end(client)

        await self.app(scope, receive, send_with_security_headers)
