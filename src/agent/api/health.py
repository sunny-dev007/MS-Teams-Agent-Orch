from fastapi import APIRouter, Query

from agent.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    deep: bool = Query(False, description="Also validate WhatsApp access token with Meta"),
) -> HealthResponse:
    whatsapp = None
    status = "healthy"
    if deep:
        from agent.services.whatsapp import check_access_token

        whatsapp = await check_access_token()
        if not whatsapp.get("ok"):
            status = "degraded"
    return HealthResponse(status=status, whatsapp=whatsapp)
