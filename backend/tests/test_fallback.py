import pytest
import asyncio
from typing import Any, List, Optional
from langchain_core.messages import HumanMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration

from backend.llm.fallback import FallbackRouter, create_fallback_chain

class MockChatModel(BaseChatModel):
    """Mock chat model for testing fallback routing logic."""
    
    response_text: str = "default response"
    should_timeout: bool = False
    
    def _generate(self, messages: Any, stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        if self.should_timeout:
            raise TimeoutError("Hard timeout triggered on model")
        message = HumanMessage(content=self.response_text)
        return ChatResult(generations=[ChatGeneration(message=message)])
        
    async def _agenerate(self, messages: Any, stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        if self.should_timeout:
            await asyncio.sleep(0.01)
            raise TimeoutError("Hard timeout triggered on model")
        message = HumanMessage(content=self.response_text)
        return ChatResult(generations=[ChatGeneration(message=message)])
        
    @property
    def _llm_type(self) -> str:
        return "mock-chat-model"

@pytest.mark.asyncio
async def test_fallback_execution_on_timeout_async():
    """
    Verify by triggering a hard timeout on the primary model 
    and observing successful fallback execution asynchronously.
    """
    primary_model = MockChatModel(should_timeout=True, response_text="primary")
    fallback_model = MockChatModel(should_timeout=False, response_text="fallback success")
    
    router = create_fallback_chain(primary_model, [fallback_model])
    
    messages = [HumanMessage(content="Hello")]
    result = await router.ainvoke(messages)
    
    assert result.content == "fallback success"

def test_fallback_execution_on_timeout_sync():
    """
    Verify synchronous fallback execution when primary times out.
    """
    primary_model = MockChatModel(should_timeout=True, response_text="primary")
    fallback_model = MockChatModel(should_timeout=False, response_text="fallback success")
    
    router = create_fallback_chain(primary_model, [fallback_model])
    
    messages = [HumanMessage(content="Hello")]
    result = router.invoke(messages)
    
    assert result.content == "fallback success"

@pytest.mark.asyncio
async def test_primary_execution_success():
    """
    Verify that if the primary model succeeds, the fallback is bypassed.
    """
    primary_model = MockChatModel(should_timeout=False, response_text="primary success")
    fallback_model = MockChatModel(should_timeout=False, response_text="fallback success")
    
    router = create_fallback_chain(primary_model, [fallback_model])
    
    messages = [HumanMessage(content="Hello")]
    result = await router.ainvoke(messages)
    
    assert result.content == "primary success"

@pytest.mark.asyncio
async def test_all_models_fail():
    """
    Verify that if both primary and fallback models fail, a consolidated exception is raised.
    """
    primary_model = MockChatModel(should_timeout=True, response_text="primary")
    fallback_model = MockChatModel(should_timeout=True, response_text="fallback")
    
    router = create_fallback_chain(primary_model, [fallback_model])
    
    messages = [HumanMessage(content="Hello")]
    
    with pytest.raises(Exception) as excinfo:
        await router.ainvoke(messages)
        
    assert "All models failed" in str(excinfo.value)
    assert "Hard timeout triggered on model" in str(excinfo.value)
