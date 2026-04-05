"""
LLM Model Factory - Multi-Model Enterprise Configuration
"""

import os
import logging
from typing import Optional, Dict
from enum import Enum
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

class AgentRole(str, Enum):
    PO = "PO"
    ARCHITECT = "Architect"
    SCRUM_MASTER = "ScrumMaster"
    DEVELOPER = "Developer"
    QA = "QA"
    SECOPS = "SecOps"
    TECH_WRITER = "TechWriter"
    ENGRAM_PROCESSOR = "EngramProcessor"

class LLMConfig(BaseModel):
    model_name: str
    temperature: float = 0.7
    max_tokens: int = 2048

class ModelFactory:
    ROLE_MODEL_MAP: Dict[AgentRole, LLMConfig] = {
        AgentRole.PO: LLMConfig(model_name="gemini-1.5-pro", temperature=0.3, max_tokens=4096),
        AgentRole.ARCHITECT: LLMConfig(model_name="gemini-1.5-pro", temperature=0.2, max_tokens=4096),
        AgentRole.SCRUM_MASTER: LLMConfig(model_name="gemini-1.5-pro", temperature=0.5, max_tokens=2048),
        AgentRole.DEVELOPER: LLMConfig(model_name="deepseek-coder", temperature=0.2, max_tokens=4096),
        AgentRole.QA: LLMConfig(model_name="deepseek-coder", temperature=0.2, max_tokens=4096),
        AgentRole.SECOPS: LLMConfig(model_name="gemini-1.5-pro", temperature=0.1, max_tokens=4096), # High precision needed
        AgentRole.TECH_WRITER: LLMConfig(model_name="gemini-1.5-flash", temperature=0.4, max_tokens=4096),
        AgentRole.ENGRAM_PROCESSOR: LLMConfig(model_name="deepseek-coder", temperature=0.1, max_tokens=2048)
    }
    
    _model_cache: Dict[AgentRole, Optional[ChatOpenAI]] = {}
    
    @classmethod
    def get_model(cls, role: AgentRole) -> ChatOpenAI:
        if role in cls._model_cache:
            return cls._model_cache[role]
        
        config = cls.ROLE_MODEL_MAP.get(role, LLMConfig(model_name="gemini-1.5-flash"))
        proxy_url = os.getenv("LLM_PROXY_URL", "http://localhost:5100")
        
        model = ChatOpenAI(
            model=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            api_key="proxy-managed",
            base_url=f"{proxy_url}/v1",
            default_headers={"X-Agent-Role": role.value}
        )
        cls._model_cache[role] = model
        return model

def get_llm_for_role(role: str) -> ChatOpenAI:
    try:
        return ModelFactory.get_model(AgentRole(role))
    except:
        return ModelFactory.get_model(AgentRole.DEVELOPER)

def create_completion(role: str, prompt: str, system_message: Optional[str] = None) -> str:
    model = get_llm_for_role(role)
    messages = []
    if system_message:
        messages.append(SystemMessage(content=system_message))
    messages.append(HumanMessage(content=prompt))
    return model.invoke(messages).content
