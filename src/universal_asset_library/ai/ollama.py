from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "ministral-3:8b"


class OllamaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OllamaStatus:
    available: bool
    models: tuple[str, ...] = ()
    diagnostic: str = ""

    def has_model(self, model: str) -> bool:
        return model in self.models or (
            ":" not in model
            and any(value.split(":", 1)[0] == model for value in self.models)
        )


@dataclass(frozen=True, slots=True)
class CategoryGuess:
    category: str
    confidence: float
    rationale: str


@dataclass(frozen=True, slots=True)
class TagGuess:
    tags: tuple[str, ...]
    confidence: float
    rationale: str


@dataclass(frozen=True, slots=True)
class Classification:
    """Compatibility result used by the standalone combined workflow."""

    category: str
    tags: tuple[str, ...]
    confidence: float
    rationale: str


class OllamaClient:
    def __init__(
        self, base_url: str = "http://127.0.0.1:11434", timeout: float = 120.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def status(self) -> OllamaStatus:
        try:
            response = self._request("GET", "/api/tags")
            records = response.get("models", [])
            models = tuple(
                str(item.get("model") or item.get("name") or "")
                for item in records
                if isinstance(item, dict) and (item.get("model") or item.get("name"))
            )
            return OllamaStatus(True, models)
        except OllamaError as error:
            return OllamaStatus(False, diagnostic=str(error))

    def pull(
        self,
        model: str,
        progress: Callable[[dict], None] | None = None,
        cancel_event: Event | None = None,
    ) -> None:
        request = Request(
            f"{self.base_url}/api/pull",
            data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(self.timeout, 3600.0)) as response:
                for raw_line in response:
                    if cancel_event and cancel_event.is_set():
                        raise OllamaError("Model download canceled.")
                    if not raw_line.strip():
                        continue
                    payload = json.loads(raw_line)
                    if isinstance(payload, dict) and payload.get("error"):
                        raise OllamaError(str(payload["error"]))
                    if progress and isinstance(payload, dict):
                        progress(payload)
        except OllamaError:
            raise
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
            raise OllamaError(f"Could not download model {model}: {error}") from error

    def guess_category(
        self,
        image_path: str | Path,
        *,
        model: str,
        categories: tuple[str, ...],
        asset_type: str,
        asset_name: str = "",
        current_category: str = "",
        current_tags: tuple[str, ...] = (),
        cancel_event: Event | None = None,
    ) -> CategoryGuess:
        canonical = tuple(
            value for value in categories if value.casefold() != "uncategorized"
        ) or categories
        if not canonical:
            raise OllamaError("At least one category is required.")
        schema = {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(canonical)},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": ["category", "confidence", "rationale"],
            "additionalProperties": False,
        }
        instruction = (
            "Select the single closest category from the supplied canonical values, "
            "even when the match is imperfect."
        )
        document = self._guess(
            Path(image_path), model, asset_type, asset_name, current_category,
            current_tags, schema, instruction,
            f"Categories: {', '.join(canonical)}", cancel_event,
            lambda value: self.validate_category_guess(value, canonical),
        )
        return document

    def guess_tags(
        self,
        image_path: str | Path,
        *,
        model: str,
        allowed_tags: tuple[str, ...],
        asset_type: str,
        asset_name: str = "",
        current_category: str = "",
        current_tags: tuple[str, ...] = (),
        cancel_event: Event | None = None,
    ) -> TagGuess:
        if len(allowed_tags) < 5:
            raise OllamaError("At least five allowed tags are required.")
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "minItems": 5,
                    "maxItems": 5,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": list(allowed_tags)},
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": ["tags", "confidence", "rationale"],
            "additionalProperties": False,
        }
        instruction = (
            "Select exactly five distinct tags from the supplied allowed values. "
            "Prefer visible subject, material, setting, action, weather, and lighting."
        )
        document = self._guess(
            Path(image_path), model, asset_type, asset_name, current_category,
            current_tags, schema, instruction,
            f"Allowed tags: {', '.join(allowed_tags)}", cancel_event,
            lambda value: self.validate_tag_guess(value, allowed_tags),
        )
        return document

    def classify(
        self,
        image_path: str | Path,
        *,
        model: str,
        categories: tuple[str, ...],
        allowed_tags: tuple[str, ...],
        asset_name: str = "",
        asset_type: str = "hdri",
        current_category: str = "",
        current_tags: tuple[str, ...] = (),
        cancel_event: Event | None = None,
    ) -> Classification:
        """Combined request retained for the standalone batch-review tool."""
        if len(allowed_tags) < 5:
            raise OllamaError("At least five allowed tags are required.")
        canonical_categories = tuple(
            value for value in categories if value.casefold() != "uncategorized"
        ) or categories
        schema = {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string", "enum": list(canonical_categories),
                },
                "tags": {
                    "type": "array", "minItems": 5, "maxItems": 5,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": list(allowed_tags)},
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
            "required": ["category", "tags", "confidence", "rationale"],
            "additionalProperties": False,
        }
        return self._guess(
            Path(image_path), model, asset_type, asset_name, current_category,
            current_tags, schema,
            "Select the closest category and exactly five distinct allowed tags.",
            (
                f"Categories: {', '.join(canonical_categories)}\n"
                f"Allowed tags: {', '.join(allowed_tags)}"
            ),
            cancel_event,
            lambda value: self.validate_classification(
                value, canonical_categories, allowed_tags
            ),
        )

    @staticmethod
    def validate_category_guess(
        content: str | dict, categories: tuple[str, ...]
    ) -> CategoryGuess:
        document = _response_object(content)
        category_map = {value.casefold(): value for value in categories}
        raw_category = str(document.get("category", "")).strip()
        category = category_map.get(raw_category.casefold())
        if not category:
            raise OllamaError(f"Category {raw_category!r} is not in the category list.")
        confidence, rationale = _confidence_and_rationale(document)
        return CategoryGuess(category, confidence, rationale)

    @staticmethod
    def validate_tag_guess(
        content: str | dict, allowed_tags: tuple[str, ...]
    ) -> TagGuess:
        document = _response_object(content)
        tags = _validated_tags(document.get("tags"), allowed_tags)
        confidence, rationale = _confidence_and_rationale(document)
        return TagGuess(tags, confidence, rationale)

    @staticmethod
    def validate_classification(
        content: str | dict,
        categories: tuple[str, ...],
        allowed_tags: tuple[str, ...],
    ) -> Classification:
        document = _response_object(content)
        category = OllamaClient.validate_category_guess(document, categories)
        tags = _validated_tags(document.get("tags"), allowed_tags)
        return Classification(
            category.category, tags, category.confidence, category.rationale
        )

    def _guess(
        self,
        image_path: Path,
        model: str,
        asset_type: str,
        asset_name: str,
        current_category: str,
        current_tags: tuple[str, ...],
        schema: dict,
        instruction: str,
        choices: str,
        cancel_event: Event | None,
        validator,
    ):
        try:
            image = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as error:
            raise OllamaError(f"Could not read preview image {image_path}: {error}") from error
        feedback = ""
        last_error = ""
        for attempt in range(2):
            if cancel_event and cancel_event.is_set():
                raise OllamaError("Analysis canceled.")
            prompt = _prompt(
                asset_type, asset_name or image_path.stem, current_category,
                current_tags, instruction, choices, feedback,
            )
            response = self._request("POST", "/api/chat", {
                "model": model,
                "messages": [{
                    "role": "user", "content": prompt, "images": [image],
                }],
                "format": schema,
                "stream": False,
                "options": {"temperature": 0},
            })
            content = response.get("message", {}).get("content", "")
            try:
                result = validator(content)
                if cancel_event and cancel_event.is_set():
                    raise OllamaError("Analysis canceled.")
                return result
            except OllamaError as error:
                last_error = str(error)
                feedback = (
                    f"Your previous response was invalid: {last_error}. Return a "
                    "corrected JSON object that follows the schema exactly."
                )
                if attempt:
                    break
        raise OllamaError(f"Model returned invalid classification twice: {last_error}")

    def _request(
        self, method: str, endpoint: str, payload: dict | None = None
    ) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                document = json.loads(response.read())
        except (HTTPError, URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise OllamaError(f"Ollama request failed: {error}") from error
        if not isinstance(document, dict):
            raise OllamaError("Ollama returned an unexpected response.")
        if document.get("error"):
            raise OllamaError(str(document["error"]))
        return document


def _response_object(content: str | dict) -> dict:
    try:
        document = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError as error:
        raise OllamaError(f"Response was not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise OllamaError("Response must be a JSON object.")
    return document


def _validated_tags(value, allowed_tags: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != 5:
        raise OllamaError("Response must contain exactly five tags.")
    tag_map = {item.casefold(): item for item in allowed_tags}
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        candidate = str(raw_tag).strip()
        tag = tag_map.get(candidate.casefold())
        if not tag:
            raise OllamaError(f"Tag {candidate!r} is not in the allowed tag list.")
        if tag.casefold() in seen:
            raise OllamaError("The five tags must be distinct.")
        seen.add(tag.casefold())
        tags.append(tag)
    return tuple(tags)


def _confidence_and_rationale(document: dict) -> tuple[float, str]:
    try:
        confidence = float(document.get("confidence"))
    except (TypeError, ValueError) as error:
        raise OllamaError("Confidence must be a number from 0 to 1.") from error
    if not 0.0 <= confidence <= 1.0:
        raise OllamaError("Confidence must be a number from 0 to 1.")
    rationale = str(document.get("rationale", "")).strip()
    if not rationale:
        raise OllamaError("Rationale must be a non-empty string.")
    return confidence, rationale


def _prompt(
    asset_type: str,
    asset_name: str,
    current_category: str,
    current_tags: tuple[str, ...],
    instruction: str,
    choices: str,
    feedback: str,
) -> str:
    labels = {
        "texture_set": "PBR texture or material",
        "atlas": "cutout atlas or decal material",
        "hdri": "HDRI environment",
        "model": "3D model",
        "stock": "stock-footage/VFX clip",
    }
    value = (
        f"Classify this rendered {labels.get(asset_type, 'visual asset')} preview. "
        f"{instruction} Base the answer on visible content. Keep the rationale "
        "to one short sentence.\n"
        f"Asset name: {asset_name}\n"
        f"Current category: {current_category or '(none)'}\n"
        f"Current tags: {', '.join(current_tags) or '(none)'}\n"
        f"{choices}"
    )
    if feedback:
        value += f"\n{feedback}"
    return value
