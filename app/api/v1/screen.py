"""`/v1/screen` — operator rentgen rasm(lar)ini yuklab, Qwen VLM skrining oladi.

Operator kamera o'rniga 1..N ta rentgen rasm(jpg/png) yuklaydi; har birini
``CargoScreener`` (Qwen3-VL) tahlil qiladi: vagon turi, asosiy yuk, konservativ
kontrabanda bayroqlari va to'liq o'zbekcha tavsif. Bu QAROR emas, operatorga
SKRINING YORDAMI.

Fail-closed: VLM ulanmagan bo'lsa (stub screener) -> 501. Jim soxta "toza" emas.
Fail-safe: bitta rasm xato bersa -> o'sha natija ok=False/error to'ldiriladi,
boshqalari davom etadi.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.auth.dependencies import require_operator, TokenClaims
from app.deps import (
    Screener,
    ServiceNotImplemented,
    provide_screener,
)
from app.errors import not_implemented

log = logging.getLogger("xray.app.screen")

router = APIRouter(tags=["screen"])

# Validatsiya cheklovlari.
_MAX_BYTES = 20 * 1024 * 1024          # 20 MB / rasm
_ALLOWED_PREFIX = "image/"
# Ba'zi brauzerlar content_type bermaydi; kengaytma bo'yicha ham tekshiramiz.
_ALLOWED_EXT = (".jpg", ".jpeg", ".png")


def _is_image(file: UploadFile) -> bool:
    ct = (file.content_type or "").lower()
    if ct.startswith(_ALLOWED_PREFIX):
        return True
    name = (file.filename or "").lower()
    return name.endswith(_ALLOWED_EXT)


@router.post(
    "/screen",
    status_code=status.HTTP_200_OK,
    summary="Rentgen rasm(lar)ini Qwen VLM orqali skrining qilish (operator yordami)",
    responses={
        200: {"description": "Har bir rasm uchun skrining natijasi (kanonik shakl)."},
        400: {"description": "Fayl yo'q, rasm emas yoki hajmi katta."},
        501: {"description": "VLM/Screener ulanmagan (fail-closed)."},
    },
)
async def screen(
    files: list[UploadFile] = File(..., description="1..N ta rentgen rasm (jpg/png)"),
    screener: Screener = Depends(provide_screener),
    claims: TokenClaims = Depends(require_operator),
) -> dict:
    """Yuklangan har bir rentgen rasmni VLM orqali skrining qiladi.

    Javob: ``{"results": [ {filename, ok, wagon_type, main_cargo, flags,
    risk_band, summary_uz, seconds, error}, ... ]}``. ``risk_band`` bayroqlardan
    deterministik hisoblanadi. Operator yakuniy qarorni o'zi qabul qiladi.
    """
    if not files:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hech qanday fayl yuklanmadi. Kamida 1 ta rasm kerak.",
        )

    # Fayllarni xotirada o'qiymiz (path traversal yo'q — disk'ga yozmaymiz,
    # filename'ni faqat metadata sifatida ishlatamiz).
    items: list[tuple[bytes, str]] = []
    for file in files:
        # Xavfsiz nom — faqat ko'rsatish uchun, faylga aylanmaydi.
        safe_name = (file.filename or "rasm").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        if not _is_image(file):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{safe_name}' rasm emas. Faqat jpg/png qabul qilinadi.",
            )

        data = await file.read()
        await file.close()

        if not data:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{safe_name}' bo'sh fayl.",
            )
        if len(data) > _MAX_BYTES:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{safe_name}' juda katta ({len(data) // (1024 * 1024)} MB > 20 MB).",
            )

        items.append((data, safe_name))

    log.info("screen: %d ta rasm operator=%s", len(items), claims.username)

    try:
        results = await screener.screen_many(items)
    except ServiceNotImplemented as exc:
        # Fail-closed: VLM ulanmagan -> 501 (jim soxta natija emas).
        raise not_implemented(exc)

    return {"results": [r.as_dict() for r in results]}
