from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.common.pagination import PageRequest, build_pagination_metadata
from app.imports.repositories.import_error_repository import ImportErrorRepository
from app.imports.repositories.import_repository import ImportRepository
from app.imports.services.get_import_status import ImportNotFoundError


class ListImportErrorsService:
    """List persisted row validation errors with deterministic pagination."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_errors(
        self,
        *,
        import_id: UUID,
        page_request: PageRequest,
    ) -> dict[str, object]:
        with self._session_factory() as session:
            import_repository = ImportRepository(session)
            if not import_repository.exists(import_id):
                raise ImportNotFoundError

            page = ImportErrorRepository(session).list_page(
                import_id=import_id,
                limit=page_request.page_size,
                offset=page_request.offset,
            )

        return {
            "items": [asdict(item) for item in page.items],
            "pagination": build_pagination_metadata(
                request=page_request,
                total_items=page.total_items,
            ).as_dict(),
        }
