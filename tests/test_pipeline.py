import asyncio
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.adapters import CameraFrame, MockAdapters, ServiceUnavailable, mock_service
from app.ai_contracts import LLMRequest, Recognition, RetrievalRequest
from app.pipeline import run_translation
from app.schemas import Mode
from vision_fixtures import encoded_image


def test_mock_llm_and_retrieval_contracts():
    adapters = MockAdapters()

    async def check():
        for operation, kwargs, expected in [
            ("contextualize", {"text": "Hello   India"}, "Hello India"),
            ("correct_grammar", {"text": "hello india"}, "Hello india."),
            ("reconstruct", {"signs": ["HELLO", "INDIA"]}, "Hello india."),
        ]:
            result = await adapters.process(LLMRequest(operation=operation, language="en-IN", **kwargs))
            assert result.text == expected
        hits = await adapters.retrieve(RetrievalRequest(query="hello", language="en-IN"))
        assert hits.hits[0].source.document_id == "greeting"
        assert hits.hits[0].relevance == 1
        empty = await adapters.retrieve(RetrievalRequest(query="unmatched", language="hi-IN"))
        assert empty.hits == []

    asyncio.run(check())


@pytest.mark.parametrize("make_invalid", [
    lambda: Recognition(text="Hello", confidence=1.1),
    lambda: LLMRequest(operation="reconstruct", language="en-IN"),
    lambda: LLMRequest(operation="correct_grammar", language="en-IN", text=" "),
    lambda: RetrievalRequest(query="hello", language="invalid!", limit=0),
])
def test_invalid_contracts(make_invalid):
    with pytest.raises(ValidationError):
        make_invalid()


@pytest.mark.parametrize("stage", ["rag", "llm"])
@pytest.mark.parametrize("slow", [False, True])
def test_context_unavailable_or_slow(stage, slow):
    class Unavailable:
        async def process(self, request):
            if slow:
                await asyncio.sleep(1)
            raise ServiceUnavailable()

        retrieve = process

    service = replace(mock_service("mock-1"), context_timeout_seconds=0.005, **{stage: Unavailable()})
    output, error = asyncio.run(run_translation(
        service, Mode.SIGN_TO_TEXT, "en-IN", 1, media=CameraFrame(encoded_image(), "image/jpeg", 0, 1),
    ))
    assert error is None
    assert output.output_text == output.recognized_text


def test_invalid_pipeline_input():
    result, error = asyncio.run(run_translation(mock_service("mock-1"), Mode.SIGN_TO_TEXT, "en-IN", 1))
    assert error.http_status == 422
    assert result.error.code == "invalid_input"
