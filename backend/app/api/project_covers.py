"""项目封面生成与下载 API"""
from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.cover_generation_service import cover_generation_service

router = APIRouter(prefix="/projects", tags=["项目封面"])


class CoverGenerateRequest(BaseModel):
    overwrite: bool = Field(default=True, description="是否覆盖已有封面")


class CoverEditRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=3000, description="封面改图要求")


class CoverGenerateResponse(BaseModel):
    project_id: str
    cover_status: str
    cover_image_url: str | None = None
    cover_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    message: str


@router.post("/{project_id}/cover/generate", response_model=CoverGenerateResponse, summary="生成项目封面")
async def generate_project_cover(
    project_id: str,
    payload: CoverGenerateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    result = await cover_generation_service.generate_cover(
        db=db,
        user_id=user_id,
        project_id=project_id,
        overwrite=payload.overwrite,
    )
    return CoverGenerateResponse(**result)


@router.post("/{project_id}/cover/edit", response_model=CoverGenerateResponse, summary="基于当前封面改图")
async def edit_project_cover(
    project_id: str,
    payload: CoverEditRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    result = await cover_generation_service.edit_cover(
        db=db,
        user_id=user_id,
        project_id=project_id,
        prompt=payload.prompt,
    )
    return CoverGenerateResponse(**result)


@router.get("/{project_id}/cover/download", summary="下载项目封面")
async def download_project_cover(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    project, target = await cover_generation_service.get_cover_download_target(
        db=db,
        user_id=user_id,
        project_id=project_id,
    )
    if isinstance(target, str):
        return RedirectResponse(url=target, status_code=307)
    file_path = target
    suffix = file_path.suffix or ".png"
    filename = f"{project.title}-cover{suffix}"
    return FileResponse(path=file_path, filename=filename, media_type="application/octet-stream")
