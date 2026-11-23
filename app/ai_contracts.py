from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas import Language

Confidence = Annotated[float, Field(ge=0, le=1)]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Recognition(BaseModel):
    text: Content
    confidence: Confidence | None = None


class RetrievalRequest(BaseModel):
    query: Content
    language: Language
    limit: int = Field(default=3, ge=1, le=10)


class Source(BaseModel):
    document_id: str
    title: str
    uri: str | None = None


class RetrievalHit(BaseModel):
    text: Content
    source: Source
    relevance: Confidence | None = None


class RetrievalResult(BaseModel):
    hits: list[RetrievalHit] = Field(default_factory=list, max_length=10)


class LLMRequest(BaseModel):
    operation: Literal["contextualize", "correct_grammar", "reconstruct"]
    language: Language
    text: Content | None = None
    signs: list[Content] = Field(default_factory=list, max_length=100)
    context: list[RetrievalHit] = Field(default_factory=list, max_length=10)

    def model_post_init(self, __context):
        if self.operation == "reconstruct" and not self.signs:
            raise ValueError("Reconstruction requires recognized signs")
        if self.operation != "reconstruct" and self.text is None:
            raise ValueError("Text is required")


class LLMResult(BaseModel):
    text: Content
