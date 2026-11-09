import logging
from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableConfig

logger = logging.getLogger(__name__)

class FallbackRouter(Runnable):
    """
    Routes LLM calls to a fallback model if the primary model fails.
    Provides explicit structured logging of fallback events for observability.
    """
    
    def __init__(self, primary: BaseChatModel, fallbacks: List[BaseChatModel]):
        self.primary = primary
        self.fallbacks = fallbacks

    def invoke(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        errors = []
        try:
            logger.info({"event": "llm_invoke_start", "model": self.primary.__class__.__name__})
            return self.primary.invoke(input, config, **kwargs)
        except Exception as e:
            logger.warning({"event": "llm_primary_failed", "error": str(e), "model": self.primary.__class__.__name__})
            errors.append(e)
            
        for i, fallback in enumerate(self.fallbacks):
            try:
                logger.info({"event": "llm_fallback_start", "model": fallback.__class__.__name__, "fallback_index": i})
                return fallback.invoke(input, config, **kwargs)
            except Exception as e:
                logger.warning({"event": "llm_fallback_failed", "error": str(e), "model": fallback.__class__.__name__, "fallback_index": i})
                errors.append(e)
                
        logger.error({"event": "llm_all_models_failed", "errors": [str(e) for e in errors]})
        raise Exception(f"All models failed. Errors: {errors}")

    async def ainvoke(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        errors = []
        try:
            logger.info({"event": "llm_ainvoke_start", "model": self.primary.__class__.__name__})
            return await self.primary.ainvoke(input, config, **kwargs)
        except Exception as e:
            logger.warning({"event": "llm_primary_failed", "error": str(e), "model": self.primary.__class__.__name__})
            errors.append(e)
            
        for i, fallback in enumerate(self.fallbacks):
            try:
                logger.info({"event": "llm_fallback_start", "model": fallback.__class__.__name__, "fallback_index": i})
                return await fallback.ainvoke(input, config, **kwargs)
            except Exception as e:
                logger.warning({"event": "llm_fallback_failed", "error": str(e), "model": fallback.__class__.__name__, "fallback_index": i})
                errors.append(e)
                
        logger.error({"event": "llm_all_models_failed", "errors": [str(e) for e in errors]})
        raise Exception(f"All models failed. Errors: {errors}")

def create_fallback_chain(primary_model: BaseChatModel, fallback_models: List[BaseChatModel]) -> Runnable:
    """
    Factory function to create a resilient chain with fallbacks.
    
    Args:
        primary_model: The primary LLM to attempt first.
        fallback_models: An ordered list of fallback LLMs to attempt if the primary fails.
        
    Returns:
        A Runnable that implements the fallback routing logic.
    """
    return FallbackRouter(primary=primary_model, fallbacks=fallback_models)
