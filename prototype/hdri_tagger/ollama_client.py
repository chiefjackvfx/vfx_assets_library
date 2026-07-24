"""Compatibility imports for the standalone prototype.

The implementation lives in the installable application package so the main UI
and prototype use identical Ollama requests and validation.
"""

from universal_asset_library.ai.ollama import (
    CategoryGuess,
    Classification,
    OllamaClient,
    OllamaError,
    OllamaStatus,
    TagGuess,
)

__all__ = [
    "CategoryGuess",
    "Classification",
    "OllamaClient",
    "OllamaError",
    "OllamaStatus",
    "TagGuess",
]
