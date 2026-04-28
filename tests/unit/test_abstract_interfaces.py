import pytest

from app.backends.base import LLMBackend
from app.platforms.base import PlatformAdapter


def test_platform_adapter_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        PlatformAdapter()


def test_llm_backend_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        LLMBackend()


def test_platform_adapter_declares_required_abstract_methods() -> None:
    assert PlatformAdapter.__abstractmethods__ == {
        "parse_incoming",
        "verify_signature",
        "send_message",
        "handle_challenge",
    }


def test_llm_backend_declares_required_abstract_methods() -> None:
    assert LLMBackend.__abstractmethods__ == {"chat", "health_check"}


def test_incomplete_platform_adapter_subclass_cannot_be_instantiated() -> None:
    class IncompletePlatformAdapter(PlatformAdapter):
        async def parse_incoming(self, raw: dict):
            raise NotImplementedError

    with pytest.raises(TypeError, match="abstract"):
        IncompletePlatformAdapter()


def test_incomplete_llm_backend_subclass_cannot_be_instantiated() -> None:
    class IncompleteLLMBackend(LLMBackend):
        async def chat(self, message, session_id: str) -> str:
            raise NotImplementedError

    with pytest.raises(TypeError, match="abstract"):
        IncompleteLLMBackend()
