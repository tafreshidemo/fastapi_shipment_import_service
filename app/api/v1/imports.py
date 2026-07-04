from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from uuid import UUID

import aiofiles
from fastapi import APIRouter, Depends, File, Header, UploadFile
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api.errors import ApiError
from app.common.pagination import InvalidPaginationError, PageRequest, parse_page_request
from app.core.settings import Settings, get_settings
from app.db.session import get_session_factory
from app.imports.services.create_import import (
    CreateImportService,
    DatabaseWriteError,
    DuplicateImportError,
    IdempotencyConflictError,
)
from app.imports.services.get_import_status import GetImportStatusService, ImportNotFoundError
from app.imports.services.list_import_errors import ListImportErrorsService
from app.storage.local_file_storage import (
    LocalFileStorage,
    StoredFile,
    UploadTooLargeError,
)

router = APIRouter(tags=["imports"])
FILE_DEFAULT = File(default=None)
IDEMPOTENCY_KEY_HEADER = Header(default=None, alias="Idempotency-Key")
SETTINGS_DEPENDENCY = Depends(get_settings)
SESSION_FACTORY_DEPENDENCY = Depends(get_session_factory)


def _validate_original_file_name(original_file_name: str | None) -> str:
    if original_file_name is None:
        raise ApiError(
            "INVALID_FILE_FORMAT",
            "Uploaded file must be a .xlsx file.",
        )

    safe_file_name = Path(original_file_name).name
    if not safe_file_name or Path(safe_file_name).suffix.lower() != ".xlsx":
        raise ApiError(
            "INVALID_FILE_FORMAT",
            "Uploaded file must be a .xlsx file.",
        )
    return safe_file_name


def _validate_idempotency_key(idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None

    if not idempotency_key or idempotency_key != idempotency_key.strip():
        raise ApiError(
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must be a non-empty textual value without surrounding whitespace.",
        )
    if len(idempotency_key) > 255 or any(
        ord(char) < 32 or ord(char) == 127 for char in idempotency_key
    ):
        raise ApiError(
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must be a non-empty textual value without surrounding whitespace.",
        )
    return idempotency_key


async def _save_upload(
    storage: LocalFileStorage,
    upload: UploadFile,
    original_file_name: str,
) -> StoredFile:
    return await storage.save_upload(reader=upload.read, original_file_name=original_file_name)


async def _has_xlsx_zip_signature(path: str) -> bool:
    async with aiofiles.open(path, "rb") as handle:
        signature = await handle.read(4)
    return signature == b"PK\x03\x04"


@router.post("/imports", status_code=HTTPStatus.ACCEPTED)
async def create_import(
    file: UploadFile | None = FILE_DEFAULT,
    idempotency_key: str | None = IDEMPOTENCY_KEY_HEADER,
    settings: Settings = SETTINGS_DEPENDENCY,
    session_factory: sessionmaker[Session] = SESSION_FACTORY_DEPENDENCY,
) -> dict[str, object]:
    if file is None:
        raise ApiError(
            "INVALID_FILE_FORMAT",
            "Uploaded file must be provided as multipart form data.",
        )

    safe_file_name = _validate_original_file_name(file.filename)
    normalized_idempotency_key = _validate_idempotency_key(idempotency_key)

    storage = LocalFileStorage(
        settings.upload_dir,
        settings.upload_read_chunk_size_bytes,
        settings.max_upload_size_bytes,
    )

    stored_file: StoredFile | None = None
    try:
        stored_file = await _save_upload(storage, file, safe_file_name)
        if not await _has_xlsx_zip_signature(stored_file.stored_file_path):
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
            raise ApiError(
                "INVALID_FILE_FORMAT",
                "Uploaded file must have a valid .xlsx ZIP signature.",
            )

        service = CreateImportService(session_factory=session_factory, settings=settings)
        outcome = await run_in_threadpool(
            service.create_import,
            original_file_name=stored_file.original_file_name,
            stored_file_path=stored_file.stored_file_path,
            file_size_bytes=stored_file.file_size_bytes,
            content_type=file.content_type,
            idempotency_key=normalized_idempotency_key,
            idempotency_fingerprint=stored_file.sha256_hex,
        )
    except UploadTooLargeError as exc:
        if stored_file is not None:
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
        raise ApiError(
            "FILE_TOO_LARGE",
            "Uploaded file exceeds the maximum allowed size.",
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        ) from exc
    except IdempotencyConflictError as exc:
        if stored_file is not None:
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
        raise ApiError(
            "IDEMPOTENCY_CONFLICT",
            "Idempotency-Key already exists for a different upload.",
            status_code=HTTPStatus.CONFLICT,
        ) from exc
    except DuplicateImportError as exc:
        if stored_file is not None:
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
        raise ApiError(
            "DUPLICATE_IMPORT",
            "An identical file was already submitted.",
            status_code=HTTPStatus.CONFLICT,
            details={
                "import_id": str(exc.existing.import_id),
                "status": exc.existing.status,
                "created_at": exc.existing.created_at.isoformat().replace("+00:00", "Z"),
            },
        ) from exc
    except DatabaseWriteError as exc:
        if stored_file is not None:
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
        raise ApiError(
            "DATABASE_ERROR",
            "Failed to persist the import.",
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        ) from exc
    except ApiError:
        if stored_file is not None:
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
        raise
    except Exception:
        if stored_file is not None:
            await storage.delete_if_exists(Path(stored_file.stored_file_path))
        raise

    if not outcome.created:
        await storage.delete_if_exists(Path(stored_file.stored_file_path))

    return asdict(outcome.result)


@router.get("/imports/{import_id}/errors")
def list_import_errors(
    import_id: UUID,
    page: str | None = None,
    page_size: str | None = None,
    settings: Settings = SETTINGS_DEPENDENCY,
    session_factory: sessionmaker[Session] = SESSION_FACTORY_DEPENDENCY,
) -> dict[str, object]:
    page_request = _parse_page_request(
        page=page,
        page_size=page_size,
        settings=settings,
    )
    try:
        return ListImportErrorsService(session_factory).list_errors(
            import_id=import_id,
            page_request=page_request,
        )
    except ImportNotFoundError as exc:
        raise _import_not_found_error() from exc


@router.get("/imports/{import_id}")
def get_import_status(
    import_id: UUID,
    session_factory: sessionmaker[Session] = SESSION_FACTORY_DEPENDENCY,
) -> dict[str, object]:
    try:
        return GetImportStatusService(session_factory).get_status(import_id)
    except ImportNotFoundError as exc:
        raise _import_not_found_error() from exc


def _parse_page_request(
    *,
    page: str | None,
    page_size: str | None,
    settings: Settings,
) -> PageRequest:
    try:
        return parse_page_request(
            page=page,
            page_size=page_size,
            default_page_size=settings.default_page_size,
            max_page_size=settings.max_page_size,
        )
    except InvalidPaginationError as exc:
        raise ApiError("INVALID_PAGINATION", str(exc)) from exc


def _import_not_found_error() -> ApiError:
    return ApiError(
        "IMPORT_NOT_FOUND",
        "Import job was not found.",
        status_code=HTTPStatus.NOT_FOUND,
    )
