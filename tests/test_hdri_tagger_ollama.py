from __future__ import annotations

import json
from pathlib import Path

import pytest

from prototype.hdri_tagger.ollama_client import OllamaClient, OllamaError


CATEGORIES = ("Outdoor", "Indoor", "Studio")
TAGS = ("sunny", "urban", "midday", "hard-light", "high-contrast", "cloudy")


def response(content) -> dict:
    return {"message": {"role": "assistant", "content": content}}


def valid_document() -> dict:
    return {
        "category": "Outdoor",
        "tags": ["sunny", "urban", "midday", "hard-light", "high-contrast"],
        "confidence": 0.82,
        "rationale": "A sunny urban exterior with direct midday light.",
    }


def test_validate_classification_canonicalizes_case() -> None:
    document = valid_document()
    document["category"] = "outdoor"
    document["tags"][0] = "SUNNY"

    result = OllamaClient.validate_classification(document, CATEGORIES, TAGS)

    assert result.category == "Outdoor"
    assert result.tags[0] == "sunny"
    assert result.confidence == pytest.approx(0.82)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value.update(category="Unknown"), "not in the category list"),
        (lambda value: value.update(tags=value["tags"][:4]), "exactly five"),
        (
            lambda value: value.update(tags=["sunny", "sunny", "midday", "hard-light", "high-contrast"]),
            "distinct",
        ),
        (lambda value: value.update(confidence=2), "0 to 1"),
    ],
)
def test_validate_classification_rejects_invalid_output(change, message) -> None:
    document = valid_document()
    change(document)

    with pytest.raises(OllamaError, match=message):
        OllamaClient.validate_classification(document, CATEGORIES, TAGS)


def test_classify_retries_once_with_validation_feedback(tmp_path, monkeypatch) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"image")
    replies = [
        response('{"category":"Unknown","tags":[]}'),
        response(json.dumps(valid_document())),
    ]
    payloads = []
    client = OllamaClient()

    def fake_request(method, endpoint, payload=None):
        payloads.append(payload)
        return replies.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.classify(
        preview, model="vision", categories=CATEGORIES, allowed_tags=TAGS
    )

    assert result.category == "Outdoor"
    assert len(payloads) == 2
    assert "previous response was invalid" in payloads[1]["messages"][0]["content"]
    assert payloads[0]["format"]["properties"]["tags"]["minItems"] == 5
    assert payloads[0]["messages"][0]["images"]


def test_classify_prompt_names_selected_asset_type(tmp_path, monkeypatch) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"image")
    client = OllamaClient()
    payloads = []

    def fake_request(method, endpoint, payload=None):
        payloads.append(payload)
        return response(json.dumps(valid_document()))

    monkeypatch.setattr(client, "_request", fake_request)
    client.classify(
        preview,
        model="vision",
        categories=CATEGORIES,
        allowed_tags=TAGS,
        asset_type="stock",
    )

    assert "stock-footage/VFX clip" in payloads[0]["messages"][0]["content"]


def test_classify_fails_after_two_invalid_responses(tmp_path, monkeypatch) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"image")
    client = OllamaClient()
    monkeypatch.setattr(
        client, "_request", lambda *args, **kwargs: response("not-json")
    )

    with pytest.raises(OllamaError, match="invalid classification twice"):
        client.classify(
            preview, model="vision", categories=CATEGORIES, allowed_tags=TAGS
        )
