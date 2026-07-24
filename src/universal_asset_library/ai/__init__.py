"""Local vision classification used by the catalog and standalone prototype."""

from .ollama import (
    DEFAULT_MODEL,
    CategoryGuess,
    Classification,
    OllamaClient,
    OllamaError,
    OllamaStatus,
    TagGuess,
)
from .vocabulary import bundled_tag_path, load_tag_vocabulary

__all__ = [
    "DEFAULT_MODEL",
    "CategoryGuess",
    "Classification",
    "OllamaClient",
    "OllamaError",
    "OllamaStatus",
    "TagGuess",
    "bundled_tag_path",
    "load_tag_vocabulary",
]
