"""Tools through the proxy: passed to every provider, read back in one shape.

The OpenAI-compatible providers take ``tools`` and answer ``tool_calls`` as
is; Gemini gets ``functionDeclarations`` and its ``functionCall`` parts come
back as ``tool_calls`` with ``finish_reason: tool_calls``, so the backend's
tool loop reads every provider alike.
"""

import json

from conftest import FakeResponse

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tracker__search",
            "description": "Search",
            "parameters": {"type": "object", "title": "Args", "properties": {"q": {"type": "string", "title": "Q"}}, "required": ["q"]},
        },
    }
]


def _chat(client, model, messages, tools=TOOLS):
    return client.post("/v1/chat/completions", json={"model": model, "messages": messages, "tools": tools})


def test_an_openai_compatible_provider_gets_the_tools_and_its_tool_calls_come_back(client, upstream):
    tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "tracker__search", "arguments": "{\"q\": \"a\"}"}}]
    upstream.set(
        "deepseek",
        FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}, "finish_reason": "tool_calls"}]}),
    )
    resp = _chat(client, "deepseek-flash", [{"role": "user", "content": "hi"}])
    assert resp.status_code == 200
    assert upstream.calls[0]["json"]["tools"] == TOOLS
    message = resp.get_json()["choices"][0]["message"]
    assert message["tool_calls"] == tool_calls and message["content"] is None
    assert resp.get_json()["usage_status"] == "estimated"


def test_gemini_gets_function_declarations_without_schema_extras(client, upstream):
    upstream.set("gemini", FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "no tools needed"}]}, "finishReason": "STOP"}]}))
    resp = _chat(client, "gemini-3.8-flash", [{"role": "user", "content": "hi"}])
    assert resp.status_code == 200
    payload = upstream.calls[0]["json"]
    assert payload["tools"] == [
        {
            "functionDeclarations": [
                {"name": "tracker__search", "description": "Search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}
            ]
        }
    ]
    choice = resp.get_json()["choices"][0]
    assert choice["message"]["content"] == "no tools needed" and choice["finish_reason"] == "stop"


def test_gemini_s_function_call_is_a_tool_call_and_the_answer_goes_back_as_a_function_response(client, upstream):
    upstream.set(
        "gemini",
        FakeResponse(
            200,
            {"candidates": [{"content": {"parts": [{"functionCall": {"name": "tracker__search", "args": {"q": "auth"}}}]}, "finishReason": "STOP"}]},
        ),
    )
    first = _chat(client, "gemini-3.8-flash", [{"role": "user", "content": "hi"}]).get_json()
    choice = first["choices"][0]
    assert choice["finish_reason"] == "tool_calls" and choice["message"]["content"] is None
    call = choice["message"]["tool_calls"][0]
    assert call["id"] == "call_1" and call["function"]["name"] == "tracker__search"
    assert json.loads(call["function"]["arguments"]) == {"q": "auth"}

    upstream.set("gemini", FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "done"}]}, "finishReason": "STOP"}]}))
    second = _chat(
        client,
        "gemini-3.8-flash",
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            choice["message"],
            {"role": "tool", "tool_call_id": "call_1", "content": "found 3"},
        ],
    )
    assert second.status_code == 200
    contents = upstream.calls[1]["json"]["contents"]
    # The answer carried no signature, so the replayed call gets the documented skip value.
    assert contents[1] == {
        "role": "model",
        "parts": [
            {
                "functionCall": {"name": "tracker__search", "args": {"q": "auth"}},
                "thoughtSignature": "skip_thought_signature_validator",
            }
        ],
    }
    assert contents[2] == {"role": "user", "parts": [{"functionResponse": {"name": "tracker__search", "response": {"content": "found 3"}}}]}
    assert upstream.calls[1]["json"]["system_instruction"] == {"parts": [{"text": "sys"}]}
    assert second.get_json()["choices"][0]["message"]["content"] == "done"


def test_a_gemini_answer_with_neither_text_nor_a_call_is_an_upstream_error(client, upstream):
    upstream.set("gemini", FakeResponse(200, {"candidates": [{"content": {"parts": [{}]}, "finishReason": "STOP"}]}))
    resp = _chat(client, "gemini-3.8-flash", [{"role": "user", "content": "hi"}])
    assert resp.status_code == 502 and "no text" in resp.get_json()["message"]


def _gemini_call(signature: str) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "workspace__list", "args": {}},
                                "thoughtSignature": signature,
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    )


def test_gemini_s_thought_signature_travels_with_the_call_and_comes_back_on_its_part(client, upstream):
    """Gemini refuses a replayed functionCall without its thoughtSignature (a 400)."""
    upstream.set("gemini", _gemini_call("sig-A"))
    first = _chat(client, "gemini-3.8-flash", [{"role": "user", "content": "hi"}]).get_json()
    call = first["choices"][0]["message"]["tool_calls"][0]
    assert call["extra_content"] == {"google": {"thought_signature": "sig-A"}}

    upstream.set("gemini", FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "done"}]}, "finishReason": "STOP"}]}))
    _chat(
        client,
        "gemini-3.8-flash",
        [
            {"role": "user", "content": "hi"},
            first["choices"][0]["message"],
            {"role": "tool", "tool_call_id": call["id"], "content": "src/"},
        ],
    )
    part = upstream.calls[1]["json"]["contents"][1]["parts"][0]
    assert part["thoughtSignature"] == "sig-A"
    assert "thoughtSignature" not in part["functionCall"]


def test_two_conversations_with_the_same_call_keep_their_own_signatures(client, upstream):
    """No state in the proxy: the same call in two tasks never borrows the other's signature."""
    upstream.set("gemini", _gemini_call("sig-task-1"))
    one = _chat(client, "gemini-3.8-flash", [{"role": "user", "content": "task 1"}]).get_json()
    upstream.set("gemini", _gemini_call("sig-task-2"))
    two = _chat(client, "gemini-3.8-flash", [{"role": "user", "content": "task 2"}]).get_json()

    upstream.set("gemini", FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}))
    for answer, expected in ((one, "sig-task-1"), (two, "sig-task-2")):
        message = answer["choices"][0]["message"]
        _chat(
            client,
            "gemini-3.8-flash",
            [
                {"role": "user", "content": "go"},
                message,
                {"role": "tool", "tool_call_id": message["tool_calls"][0]["id"], "content": "x"},
            ],
        )
        assert upstream.calls[-1]["json"]["contents"][1]["parts"][0]["thoughtSignature"] == expected
