# LLM Fallback Routing API

## Overview
The `backend.llm.fallback` module provides resilient execution by routing orchestration requests to secondary models when the primary model fails (e.g., due to a hard timeout or a 502 Bad Gateway).

## Components

### `FallbackRouter`
A LangChain-compatible `Runnable` that sequentially attempts a primary model followed by a list of fallback models.

**Features:**
- Implements both `invoke` and `ainvoke` for synchronous and asynchronous execution.
- Emits structured logs for observability (`llm_invoke_start`, `llm_primary_failed`, `llm_fallback_start`, `llm_all_models_failed`).
- Raises a consolidated exception if all models fail, preserving the error chain for debugging.

### `create_fallback_chain(primary_model: BaseChatModel, fallback_models: List[BaseChatModel]) -> Runnable`
Factory function to instantiate a `FallbackRouter`.

## Usage Example
```python
from backend.llm.fallback import create_fallback_chain
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

primary = ChatOpenAI(model="gpt-4o")
fallback = ChatGoogleGenerativeAI(model="gemini-1.5-pro")

# Create a resilient chain that falls back to Gemini if OpenAI fails
resilient_chain = create_fallback_chain(primary, [fallback])

# Execute asynchronously
response = await resilient_chain.ainvoke([HumanMessage(content="Hello")])
