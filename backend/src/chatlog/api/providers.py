from fastapi import APIRouter, Header, HTTPException, status

from chatlog.api.schemas import ProviderInfo, ProviderModelsResponse, ProvidersResponse
from chatlog.config import get_settings
from chatlog.providers import (
    PROVIDERS,
    ProviderModelsError,
    fetch_provider_models,
    get_provider,
    is_provider_configured,
    pick_default_model,
    resolve_api_key,
)

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    settings = get_settings()
    providers = [
        ProviderInfo(
            id=spec.id,
            label=spec.label,
            default_model=spec.default_model,
            requires_api_key=spec.auth == "client",
            configured=is_provider_configured(spec, settings),
        )
        for spec in PROVIDERS.values()
    ]
    return ProvidersResponse(providers=providers)


@router.get("/{provider_id}/models", response_model=ProviderModelsResponse)
async def list_provider_models(
    provider_id: str,
    x_provider_api_key: str | None = Header(default=None, alias="X-Provider-Api-Key"),
) -> ProviderModelsResponse:
    settings = get_settings()
    try:
        spec = get_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        api_key = resolve_api_key(spec, settings, x_provider_api_key)
        models = await fetch_provider_models(
            spec,
            api_key,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ProviderModelsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ProviderModelsResponse(
        provider=spec.id,
        default_model=pick_default_model(models, spec.default_model),
        models=models,
    )
