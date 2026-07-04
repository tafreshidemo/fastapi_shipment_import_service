from __future__ import annotations

import logging
from collections.abc import Iterator
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.imports.parsers.errors import WorkbookStructureError
from app.imports.parsers.xlsx_parser import XlsxParser
from app.imports.repositories.import_claim_repository import ClaimedImport, ImportClaimRepository
from app.imports.repositories.import_error_repository import ImportErrorRepository
from app.imports.repositories.import_progress_repository import ImportProgressRepository
from app.imports.repositories.shipment_repository import ShipmentRepository
from app.imports.services.row_validation import RowValidationService, ValidationChunkResult

logger = logging.getLogger(__name__)


class RetryableImportProcessingError(Exception):
    """Signals that the Celery task should retry after the job was re-queued."""


class StaleImportWorkerError(Exception):
    """Signals that this worker no longer owns the import token."""


class ProcessImportService:
    """Synchronously process one claimed import in short chunk transactions."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        worker_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._worker_id = worker_id

    def run(self, import_id: UUID | str) -> None:
        normalized_import_id = UUID(str(import_id))
        processing_token = uuid4()

        with self._session_factory() as session:
            claim_repository = ImportClaimRepository(session)
            progress_repository = ImportProgressRepository(session)
            shipment_repository = ShipmentRepository(session)
            import_error_repository = ImportErrorRepository(session)
            claimed_import: ClaimedImport | None = None

            try:
                with session.begin():
                    claimed_import = claim_repository.claim_pending_import(
                        import_id=normalized_import_id,
                        processing_token=processing_token,
                        worker_id=self._worker_id,
                    )

                if claimed_import is None:
                    return

                self._prepare_for_reprocessing(
                    session=session,
                    progress_repository=progress_repository,
                    shipment_repository=shipment_repository,
                    import_error_repository=import_error_repository,
                    claimed_import=claimed_import,
                )

                validator = RowValidationService(
                    XlsxParser(
                        claimed_import.stored_file_path,
                        chunk_size=self._settings.processing_row_chunk_size,
                    ),
                    shipment_repository,
                )
                validated_chunks = validator.iter_validated_chunks(
                    import_id=claimed_import.import_id
                )

                total_rows = 0
                success_count = 0
                failed_count = 0
                while True:
                    chunk_result = self._process_next_chunk(
                        session=session,
                        progress_repository=progress_repository,
                        shipment_repository=shipment_repository,
                        import_error_repository=import_error_repository,
                        validator=validator,
                        validated_chunks=validated_chunks,
                        claimed_import=claimed_import,
                    )
                    if chunk_result is None:
                        break
                    total_rows += chunk_result.total_rows
                    success_count += chunk_result.success_count
                    failed_count += chunk_result.failed_count

                with session.begin():
                    if not progress_repository.complete(
                        import_id=claimed_import.import_id,
                        processing_token=claimed_import.processing_token,
                        total_rows=total_rows,
                        success_count=success_count,
                        failed_count=failed_count,
                    ):
                        raise StaleImportWorkerError
                print(f"claim import: {str(claimed_import)}")
                logger.info("Import completed", extra={"import_id": str(claimed_import.import_id)})
            except StaleImportWorkerError:
                logger.info(
                    "Import worker stopped after losing ownership",
                    extra={"import_id": str(normalized_import_id)},
                )
            except WorkbookStructureError as exc:
                if claimed_import is not None:
                    logger.exception(
                        "Workbook structure processing failed",
                        extra={
                            "import_id": str(claimed_import.import_id),
                            "stored_file_path": str(claimed_import.stored_file_path),
                        },
                    )
                    self._mark_failed(
                        session=session,
                        progress_repository=progress_repository,
                        claimed_import=claimed_import,
                        reason=str(exc) or "Workbook structure could not be processed.",
                    )
            except OperationalError as exc:
                if claimed_import is None:
                    raise RetryableImportProcessingError(
                        "Database operation failed before claim."
                    ) from exc
                self._handle_operational_failure(
                    session=session,
                    progress_repository=progress_repository,
                    claimed_import=claimed_import,
                )
            except (IntegrityError, SQLAlchemyError, OSError, ValueError):
                if claimed_import is not None:
                    self._mark_failed(
                        session=session,
                        progress_repository=progress_repository,
                        claimed_import=claimed_import,
                        reason="Import processing failed.",
                    )
                else:
                    raise
            except Exception:
                if claimed_import is not None:
                    self._mark_failed(
                        session=session,
                        progress_repository=progress_repository,
                        claimed_import=claimed_import,
                        reason="Import processing failed.",
                    )
                else:
                    raise

    def _prepare_for_reprocessing(
        self,
        *,
        session: Session,
        progress_repository: ImportProgressRepository,
        shipment_repository: ShipmentRepository,
        import_error_repository: ImportErrorRepository,
        claimed_import: ClaimedImport,
    ) -> None:
        with session.begin():
            if not progress_repository.reset_for_reprocessing(
                import_id=claimed_import.import_id,
                processing_token=claimed_import.processing_token,
            ):
                raise StaleImportWorkerError
            shipment_repository.delete_by_import_id(claimed_import.import_id)
            import_error_repository.delete_by_import_id(claimed_import.import_id)

    def _process_next_chunk(
        self,
        *,
        session: Session,
        progress_repository: ImportProgressRepository,
        shipment_repository: ShipmentRepository,
        import_error_repository: ImportErrorRepository,
        validator: RowValidationService,
        validated_chunks: Iterator[ValidationChunkResult],
        claimed_import: ClaimedImport,
    ) -> ValidationChunkResult | None:
        chunk_result: ValidationChunkResult | None = None
        try:
            with session.begin():
                self._require_current_ownership(progress_repository, claimed_import)
                try:
                    chunk_result = next(validated_chunks)
                except StopIteration:
                    return None
                self._persist_chunk(
                    shipment_repository=shipment_repository,
                    import_error_repository=import_error_repository,
                    progress_repository=progress_repository,
                    claimed_import=claimed_import,
                    chunk_result=chunk_result,
                )
            print(f"[_process_next_chunk] --> chunk_result: {chunk_result}")
            return chunk_result
        except IntegrityError as exc:
            if chunk_result is None:
                raise
            return self._persist_chunk_after_duplicate_race(
                session=session,
                progress_repository=progress_repository,
                shipment_repository=shipment_repository,
                import_error_repository=import_error_repository,
                validator=validator,
                claimed_import=claimed_import,
                chunk_result=chunk_result,
                original_error=exc,
            )

    def _persist_chunk_after_duplicate_race(
        self,
        *,
        session: Session,
        progress_repository: ImportProgressRepository,
        shipment_repository: ShipmentRepository,
        import_error_repository: ImportErrorRepository,
        validator: RowValidationService,
        claimed_import: ClaimedImport,
        chunk_result: ValidationChunkResult,
        original_error: IntegrityError,
    ) -> ValidationChunkResult:
        current_result = chunk_result
        while True:
            corrected_result: ValidationChunkResult | None = None
            try:
                with session.begin():
                    self._require_current_ownership(progress_repository, claimed_import)
                    corrected_result = validator.reclassify_database_duplicates(
                        import_id=claimed_import.import_id,
                        chunk_result=current_result,
                    )
                    if corrected_result.success_count == current_result.success_count:
                        break
                    self._persist_chunk(
                        shipment_repository=shipment_repository,
                        import_error_repository=import_error_repository,
                        progress_repository=progress_repository,
                        claimed_import=claimed_import,
                        chunk_result=corrected_result,
                    )
                return corrected_result
            except IntegrityError:
                if corrected_result is None:
                    raise
                current_result = corrected_result

        raise original_error

    def _persist_chunk(
        self,
        *,
        shipment_repository: ShipmentRepository,
        import_error_repository: ImportErrorRepository,
        progress_repository: ImportProgressRepository,
        claimed_import: ClaimedImport,
        chunk_result: ValidationChunkResult,
    ) -> None:
        shipment_repository.bulk_insert(chunk_result.shipments)
        import_error_repository.bulk_insert(chunk_result.import_errors)
        if not progress_repository.record_chunk_counts(
            import_id=claimed_import.import_id,
            processing_token=claimed_import.processing_token,
            total_rows=chunk_result.total_rows,
            success_count=chunk_result.success_count,
            failed_count=chunk_result.failed_count,
        ):
            raise StaleImportWorkerError
        if not progress_repository.heartbeat(
            import_id=claimed_import.import_id,
            processing_token=claimed_import.processing_token,
        ):
            raise StaleImportWorkerError

    def _handle_operational_failure(
        self,
        *,
        session: Session,
        progress_repository: ImportProgressRepository,
        claimed_import: ClaimedImport,
    ) -> None:
        if claimed_import.attempt_count >= claimed_import.max_attempts:
            self._mark_failed(
                session=session,
                progress_repository=progress_repository,
                claimed_import=claimed_import,
                reason="Import processing failed after the maximum number of attempts.",
            )
            return

        with session.begin():
            requeued = progress_repository.requeue_for_retry(
                import_id=claimed_import.import_id,
                processing_token=claimed_import.processing_token,
                reason="Import processing encountered a temporary database error.",
            )
        if not requeued:
            logger.info(
                "Import worker stopped after losing ownership",
                extra={"import_id": str(claimed_import.import_id)},
            )
            return
        raise RetryableImportProcessingError("Import processing will be retried.")

    def _mark_failed(
        self,
        *,
        session: Session,
        progress_repository: ImportProgressRepository,
        claimed_import: ClaimedImport,
        reason: str,
    ) -> None:
        with session.begin():
            failed = progress_repository.fail(
                import_id=claimed_import.import_id,
                processing_token=claimed_import.processing_token,
                reason=reason,
            )
        if not failed:
            logger.info(
                "Import worker stopped after losing ownership",
                extra={"import_id": str(claimed_import.import_id)},
            )
            return
        logger.error("Import failed", extra={"import_id": str(claimed_import.import_id)})

    @staticmethod
    def _require_current_ownership(
        progress_repository: ImportProgressRepository,
        claimed_import: ClaimedImport,
    ) -> None:
        if not progress_repository.has_current_ownership(
            import_id=claimed_import.import_id,
            processing_token=claimed_import.processing_token,
        ):
            raise StaleImportWorkerError
