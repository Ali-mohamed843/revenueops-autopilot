"""Builds the configured store adapter: the only module that knows which stores exist."""

from revenueops.adapters.base import StoreAdapter
from revenueops.config import Settings


def build_adapter(settings: Settings) -> StoreAdapter:
    if settings.store_adapter == "storeforge":
        from revenueops.adapters.storeforge import StoreForgeAdapter

        if not settings.storeforge_api_key:
            raise ValueError("STOREFORGE_API_KEY is not set (it must equal INTEGRATION_API_KEY in StoreForge)")
        return StoreForgeAdapter(settings.storeforge_api_url, settings.storeforge_api_key)
    raise ValueError(f"Unknown STORE_ADAPTER: {settings.store_adapter!r}")
