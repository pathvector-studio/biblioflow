"""External bibliographic metadata resolution (arXiv / CrossRef / Semantic Scholar)."""

from app.schemas.metadata import MetadataResolver


def get_resolver() -> MetadataResolver:
    """FastAPI dependency / worker entry point. Tests override this via ``app.dependency_overrides``."""
    from app.services.metadata.resolver import build_default_resolver

    return build_default_resolver()
