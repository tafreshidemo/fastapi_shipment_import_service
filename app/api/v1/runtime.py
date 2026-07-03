from fastapi import APIRouter

router = APIRouter(tags=["runtime"])


@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
