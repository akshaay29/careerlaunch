"""profile router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

Resume versions (FR-3): upload, list, activate, download (signed URL) and delete. Every
``{resume_id}`` route answers 404 for another Student's id and for an unknown id.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor
from app.core.config import get_settings
from app.core.db import get_session
from app.core.jobs import PRIORITY_QUEUE, drain
from app.modules.profile import service
from app.modules.profile.files import MAX_FILE_BYTES
from app.modules.profile.models import Resume
from app.modules.profile.schemas import ResumeDownload, ResumeList, ResumeVersion
from app.ports.file_store import FileStore, file_store_dependency

logger = logging.getLogger("app.profile")

router = APIRouter(tags=["profile"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]
Store = Annotated[FileStore, Depends(file_store_dependency)]

# The after-response drain of the priority queue is short and bounded (AD-4 priority path).
PRIORITY_DRAIN_TIMEOUT_SECONDS = 30

_PROBLEM = {"content": {"application/problem+json": {}}}
_NOT_FOUND = {404: {"description": "No such Resume version for this Student", **_PROBLEM}}


async def drain_priority() -> None:
    """Bounded in-process drain of the ``priority`` queue, run after the response."""
    try:
        await drain(queues=(PRIORITY_QUEUE,), timeout=PRIORITY_DRAIN_TIMEOUT_SECONDS)
    except Exception:
        logger.exception("priority_drain_failed")


def _version(resume: Resume) -> ResumeVersion:
    return ResumeVersion.model_validate(resume, from_attributes=True)


@router.post(
    "/resumes",
    status_code=status.HTTP_201_CREATED,
    response_model=ResumeVersion,
    operation_id="uploadResume",
    responses={
        409: {"description": "The version cap is reached", **_PROBLEM},
        413: {"description": "The file is larger than 5 MB", **_PROBLEM},
        415: {"description": "The file is not a PDF or DOCX", **_PROBLEM},
    },
)
async def upload_resume(
    file: Annotated[UploadFile, File(description="A PDF or DOCX file of 5 MB or less")],
    actor: CurrentActor,
    session: Session,
    store: Store,
) -> ResumeVersion:
    """Upload a new Resume version. The type is read from the file's content. It becomes
    the active version only when the Student has no active Resume."""
    try:
        # One byte over the limit is enough to know it is too large.
        data = await file.read(MAX_FILE_BYTES + 1)
    finally:
        await file.close()
    resume = await service.upload_resume(
        session,
        store,
        actor.id,
        filename=file.filename,
        data=data,
        max_versions=get_settings().resume_max_versions,
    )
    try:
        await session.commit()
    except BaseException:  # cancellation too: never orphan a private file
        await service.discard_upload(store, resume)
        raise
    return _version(resume)


@router.get("/resumes", response_model=ResumeList, operation_id="listResumes")
async def list_resumes(
    actor: CurrentActor,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[uuid.UUID | None, Query()] = None,
) -> ResumeList:
    page = await service.list_resumes(session, actor.id, limit=limit, cursor=cursor)
    return ResumeList(items=[_version(r) for r in page.items], next_cursor=page.next_cursor)


@router.post(
    "/resumes/{resume_id}/activate",
    response_model=ResumeVersion,
    operation_id="activateResume",
    responses=_NOT_FOUND,
)
async def activate_resume(
    resume_id: uuid.UUID, actor: CurrentActor, session: Session
) -> ResumeVersion:
    resume, changed = await service.activate_resume(session, actor.id, resume_id)
    if changed:
        await session.commit()
    return _version(resume)


@router.get(
    "/resumes/{resume_id}/download",
    response_model=ResumeDownload,
    operation_id="getResumeDownload",
    responses=_NOT_FOUND,
)
async def get_resume_download(
    resume_id: uuid.UUID, actor: CurrentActor, session: Session, store: Store
) -> ResumeDownload:
    """A signed URL (5 minutes or less) that downloads the file as an attachment."""
    link = await service.download_link(session, store, actor.id, resume_id)
    return ResumeDownload(url=link.url, expires_at=link.expires_at)


@router.delete(
    "/resumes/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    operation_id="deleteResume",
    responses=_NOT_FOUND,
)
async def delete_resume(
    resume_id: uuid.UUID, actor: CurrentActor, session: Session, background: BackgroundTasks
) -> Response:
    """Delete a version. Its file is removed by a background job started right after the
    response. Deleting the active version leaves no version active."""
    await service.delete_resume(session, actor.id, resume_id)
    await session.commit()
    background.add_task(drain_priority)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
