from __future__ import annotations

import json

import pytest

from universal_asset_library.ai import (
    CategoryGuess,
    OllamaClient,
    OllamaError,
    TagGuess,
    load_tag_vocabulary,
)


CATEGORIES = ("Outdoor", "Indoor", "Uncategorized")
TAGS = ("sunny", "urban", "midday", "hard-light", "high-contrast", "cloudy")


def response(document) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": json.dumps(document) if not isinstance(document, str) else document,
        }
    }


def test_category_guess_uses_category_only_schema_and_excludes_uncategorized(
    tmp_path, monkeypatch,
) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"image")
    payloads = []
    client = OllamaClient()

    def fake_request(method, endpoint, payload=None):
        payloads.append(payload)
        return response({
            "category": "Outdoor",
            "confidence": 0.8,
            "rationale": "A sunlit exterior.",
        })

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.guess_category(
        preview,
        model="vision",
        categories=CATEGORIES,
        asset_type="hdri",
    )

    assert result == CategoryGuess("Outdoor", 0.8, "A sunlit exterior.")
    properties = payloads[0]["format"]["properties"]
    assert "tags" not in properties
    assert properties["category"]["enum"] == ["Outdoor", "Indoor"]
    assert payloads[0]["options"]["temperature"] == 0


def test_tag_guess_uses_tag_only_schema_and_retries_invalid_output(
    tmp_path, monkeypatch,
) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"image")
    replies = [
        response({"tags": ["sunny"], "confidence": 1, "rationale": "Bad"}),
        response({
            "tags": ["sunny", "urban", "midday", "hard-light", "high-contrast"],
            "confidence": 0.75,
            "rationale": "Five visible descriptors.",
        }),
    ]
    payloads = []
    client = OllamaClient()

    def fake_request(method, endpoint, payload=None):
        payloads.append(payload)
        return replies.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.guess_tags(
        preview,
        model="vision",
        allowed_tags=TAGS,
        asset_type="texture_set",
    )

    assert result == TagGuess(
        ("sunny", "urban", "midday", "hard-light", "high-contrast"),
        0.75,
        "Five visible descriptors.",
    )
    assert "category" not in payloads[0]["format"]["properties"]
    assert "previous response was invalid" in payloads[1]["messages"][0]["content"]


def test_separate_guess_validators_reject_unknown_values() -> None:
    with pytest.raises(OllamaError, match="not in the category list"):
        OllamaClient.validate_category_guess({
            "category": "Sky",
            "confidence": 0.5,
            "rationale": "No",
        }, CATEGORIES)
    with pytest.raises(OllamaError, match="not in the allowed tag list"):
        OllamaClient.validate_tag_guess({
            "tags": ["sunny", "urban", "midday", "hard-light", "invented"],
            "confidence": 0.5,
            "rationale": "No",
        }, TAGS)


def test_packaged_vocabularies_and_stock_override(tmp_path) -> None:
    for asset_type in ("texture_set", "atlas", "hdri", "model", "stock"):
        assert len(load_tag_vocabulary(asset_type)) >= 5
    control = tmp_path / ".ual"
    control.mkdir()
    (control / "stock_tags.json").write_text(json.dumps({
        "tags": [
            {"name": "one"}, {"name": "two"}, {"name": "three"},
            {"name": "four"}, {"name": "five"},
        ],
    }), encoding="utf-8")

    assert load_tag_vocabulary("stock", tmp_path) == (
        "one", "two", "three", "four", "five",
    )
