"""Provider-neutral text embedding adapter used by indexing and retrieval."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, FiniteFloat, ValidationError, field_validator, model_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"


class EmbedderError(RuntimeError):
    """Raised when an embedding provider cannot return valid vectors."""


class _EmbeddingRequest(BaseModel):
    """Validated provider-neutral input while preserving the public function API."""

    model_config = ConfigDict(strict=True, extra="forbid")

    texts: list[str]
    model: str
    provider: Literal["gemini", "openai"] | None = None
    task_type: str | None = None

    @field_validator("texts")
    @classmethod
    def validate_texts(cls, texts: list[str]) -> list[str]:
        if any(not text.strip() for text in texts):
            raise ValueError("mọi phần tử phải là chuỗi không rỗng")
        return texts

    @field_validator("model")
    @classmethod
    def validate_model(cls, model: str) -> str:
        if not model.strip():
            raise ValueError("model không được để trống")
        return model.strip()

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, provider: object) -> object:
        return provider.strip().lower() if isinstance(provider, str) else provider

    @field_validator("task_type")
    @classmethod
    def validate_task_type(cls, task_type: str | None) -> str | None:
        if task_type is None:
            return None
        if not task_type.strip():
            raise ValueError("task_type không được là chuỗi rỗng")
        return task_type.strip()


class _EmbeddingBatch(BaseModel):
    """Provider response contract: N finite vectors with one shared dimension."""

    vectors: list[list[FiniteFloat]]
    expected_count: int

    @model_validator(mode="after")
    def validate_shape(self) -> "_EmbeddingBatch":
        if len(self.vectors) != self.expected_count:
            raise ValueError(
                f"provider trả về {len(self.vectors)} vector cho "
                f"{self.expected_count} văn bản"
            )
        if not self.vectors:
            return self
        dimension = len(self.vectors[0])
        if dimension == 0 or any(len(vector) != dimension for vector in self.vectors):
            raise ValueError("vector rỗng hoặc không đồng nhất số chiều")
        return self


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in errors
    )


def infer_provider(model: str) -> str:
    normalized = model.lower()
    if normalized.startswith("gemini-") or normalized.startswith("models/gemini-"):
        return "gemini"
    if normalized.startswith("text-embedding-"):
        return "openai"
    raise EmbedderError(
        f"Không xác định được provider từ model '{model}'. "
        "Hãy truyền provider='gemini' hoặc provider='openai'."
    )


def _validate_vectors(vectors: Sequence[Sequence[float]], expected: int) -> list[list[float]]:
    try:
        batch = _EmbeddingBatch.model_validate(
            {"vectors": vectors, "expected_count": expected}
        )
    except ValidationError as exc:
        raise EmbedderError("Embedding không hợp lệ: " + _validation_message(exc)) from exc
    return [[float(value) for value in vector] for vector in batch.vectors]


def _embed_openai(texts: list[str], model: str) -> list[list[float]]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise EmbedderError("Chưa cài package 'openai'. Chạy: pip install openai") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EmbedderError("Thiếu biến môi trường OPENAI_API_KEY.")
    try:
        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(model=model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return _validate_vectors([item.embedding for item in ordered], len(texts))
    except EmbedderError:
        raise
    except Exception as exc:  # provider SDK errors vary by version
        raise EmbedderError(f"OpenAI embedding thất bại: {exc}") from exc


def _embed_gemini(texts: list[str], model: str, task_type: str | None) -> list[list[float]]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise EmbedderError("Chưa cài package 'google-genai'. Chạy: pip install google-genai") from exc

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EmbedderError("Thiếu biến môi trường GEMINI_API_KEY.")
    try:
        config = types.EmbedContentConfig(task_type=task_type) if task_type else None
        # Giữ strong reference tới client trong suốt request. Nếu gọi trực tiếp trên
        # object tạm, garbage collector có thể đóng HTTP client giữa lúc SDK retry.
        client = genai.Client(api_key=api_key)
        response = client.models.embed_content(
            model=model,
            contents=texts,
            config=config,
        )
        embeddings = response.embeddings or []
        return _validate_vectors([item.values for item in embeddings], len(texts))
    except EmbedderError:
        raise
    except Exception as exc:  # provider SDK errors vary by version
        raise EmbedderError(f"Gemini embedding thất bại: {exc}") from exc


def embed_texts(
    texts: list[str],
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider: str | None = None,
    task_type: str | None = None,
) -> list[list[float]]:
    """Embed a batch of non-empty texts with Gemini or OpenAI."""
    try:
        request = _EmbeddingRequest.model_validate(
            {
                "texts": texts,
                "model": model,
                "provider": provider,
                "task_type": task_type,
            },
            strict=True,
        )
    except ValidationError as exc:
        raise EmbedderError("Request embedding không hợp lệ: " + _validation_message(exc)) from exc
    if not request.texts:
        return []

    selected = request.provider or infer_provider(request.model)
    if selected == "openai":
        return _embed_openai(request.texts, request.model)
    if selected == "gemini":
        return _embed_gemini(request.texts, request.model, request.task_type)
    raise EmbedderError(f"Provider không được hỗ trợ: '{selected}'.")
