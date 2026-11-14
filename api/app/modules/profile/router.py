"""profile router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

Resume versions (FR-3): upload, list, activate, download (signed URL) and delete. Every
``{resume_id}`` route answers 404 for another Student's id and for an unknown id.

Skill Profile (FR-4): read, edit (add / remove / remove custom), reset, and vocabulary
suggestions. Upload, activate, edit and reset start the bounded after-response drain of the
``priority`` queue (AD-4 priority path), which runs the extraction job.

Preferences (FR-5): read (the defaults with ``saved: false`` before the first save), replace
the whole object, and the canonical degree list for the form. A save that changes something
starts the same after-response drain.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import degrees, skills
from app.core.auth import Actor, current_actor
from app.core.config import get_settings
from app.core.db import get_session
from app.core.jobs import PRIORITY_QUEUE, drain
from app.modules.profile import service
from app.modules.profile.files import MAX_FILE_BYTES
from app.modules.profile.models import Preferences, Resume, SkillProfile
from app.modules.profile.schemas import (
    DegreeOut,
    PreferencesIn,
    PreferencesOut,
    ProfileSkill,
    ResumeDownload,
    ResumeList,
    ResumeVersion,
    SkillProfileEdit,
    SkillProfileOut,
    SkillRef,
)
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
    background: BackgroundTasks,
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
    # A first (active) upload enqueued the Skill Profile extraction.
    background.add_task(drain_priority)
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
    resume_id: uuid.UUID, actor: CurrentActor, session: Session, background: BackgroundTasks
) -> ResumeVersion:
    resume, changed = await service.activate_resume(session, actor.id, resume_id)
    if changed:
        await session.commit()
        background.add_task(drain_priority)
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


# --------------------------------------------------------------------------- Skill Profile


def _skill_profile(profile: SkillProfile | None) -> SkillProfileOut:
    if profile is None:
        return SkillProfileOut(
            status="none",
            resume_id=None,
            skills=[],
            removed=[],
            custom=[],
            education=[],
            experience=[],
            vocab_version=None,
            message=None,
            has_edits=False,
        )
    extracted = set(profile.extracted_skills)
    status = profile.status or "none"
    return SkillProfileOut(
        status=status,  # type: ignore[arg-type]
        resume_id=profile.resume_id,
        skills=[
            ProfileSkill(
                id=skill_id,
                label=skills.label(skill_id),
                source="extracted" if skill_id in extracted else "added",
            )
            for skill_id in skills.sorted_ids(service.effective_skills(profile))
        ],
        removed=[
            SkillRef(id=skill_id, label=skills.label(skill_id))
            for skill_id in skills.sorted_ids(profile.removed_skills)
        ],
        custom=list(profile.custom_skills),
        education=[str(e) for e in profile.education],
        experience=[str(e) for e in profile.experience],
        vocab_version=profile.vocab_version,
        message=service.UNREADABLE_MESSAGE if status in ("unreadable", "failed") else None,
        has_edits=service.has_edits(profile),
    )


@router.get("/skill-profile", response_model=SkillProfileOut, operation_id="getSkillProfile")
async def get_skill_profile(actor: CurrentActor, session: Session) -> SkillProfileOut:
    return _skill_profile(await service.get_skill_profile(session, actor.id))


@router.patch(
    "/skill-profile",
    response_model=SkillProfileOut,
    operation_id="editSkillProfile",
    responses={422: {"description": "Invalid terms, ids or too many custom skills", **_PROBLEM}},
)
async def edit_skill_profile(
    body: SkillProfileEdit, actor: CurrentActor, session: Session, background: BackgroundTasks
) -> SkillProfileOut:
    """Add skills (a vocabulary term becomes a skill, anything else a custom skill), remove
    skills by id, or remove custom skills. Edits are kept when a new Resume is extracted."""
    profile, changed = await service.edit_skill_profile(
        session, actor.id, add=body.add, remove=body.remove, remove_custom=body.remove_custom
    )
    if changed:
        await session.commit()
        background.add_task(drain_priority)
    return _skill_profile(profile)


@router.post(
    "/skill-profile/reset", response_model=SkillProfileOut, operation_id="resetSkillProfile"
)
async def reset_skill_profile(
    actor: CurrentActor, session: Session, background: BackgroundTasks
) -> SkillProfileOut:
    """Reset to extracted: drop every added, removed and custom skill."""
    profile, changed = await service.reset_skill_profile(session, actor.id)
    if changed:
        await session.commit()
        background.add_task(drain_priority)
    return _skill_profile(profile)


@router.get("/skills", response_model=list[SkillRef], operation_id="suggestSkills")
async def suggest_skills(
    q: Annotated[str, Query(min_length=1, max_length=50)],
) -> list[SkillRef]:
    """Up to 20 vocabulary skills matching ``q`` (prefix matches first), for autocomplete."""
    return [SkillRef(id=s.id, label=s.label) for s in skills.suggest(q, limit=20)]


# --------------------------------------------------------------------------- Preferences


def _preferences(prefs: Preferences | None) -> PreferencesOut:
    values = service.preference_values(prefs)
    if values["cgpa"] is not None:
        values["cgpa"] = float(values["cgpa"])
    return PreferencesOut(
        **values,
        saved=prefs is not None,
        updated_at=prefs.updated_at if prefs is not None else None,
    )


@router.get("/preferences", response_model=PreferencesOut, operation_id="getPreferences")
async def get_preferences(actor: CurrentActor, session: Session) -> PreferencesOut:
    """The Student's Preferences, or the defaults with ``saved: false`` before the first
    save."""
    return _preferences(await service.get_preferences(session, actor.id))


@router.put(
    "/preferences",
    response_model=PreferencesOut,
    operation_id="savePreferences",
    responses={422: {"description": "A field is missing or invalid", **_PROBLEM}},
)
async def save_preferences(
    body: PreferencesIn, actor: CurrentActor, session: Session, background: BackgroundTasks
) -> PreferencesOut:
    """Replace the whole Preferences object. Saving the same values again changes
    nothing."""
    prefs, changed = await service.save_preferences(session, actor.id, body.model_dump())
    if changed:
        await session.commit()
        background.add_task(drain_priority)
    return _preferences(prefs)


@router.get("/degrees", response_model=list[DegreeOut], operation_id="listDegrees")
async def list_degrees() -> list[DegreeOut]:
    """The canonical degrees for the Preferences form, in display order."""
    return [DegreeOut(id=d.id, label=d.label) for d in degrees.all_degrees()]
