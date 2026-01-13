import os

from src.workers import celery_app
from src.services.output_builder import build_outputs
from src.services import order_service
from loguru import logger

USE_CELERY = os.getenv("USE_CELERY", "false").lower() == "true"


class OutputBuildError(RuntimeError):
    pass


def _run_outputs_inline(order_id: str):
    try:
        build_outputs(order_id)
    except Exception as exc:  # noqa: BLE001
        order_service.set_status(order_id, "エラー")
        raise OutputBuildError(str(exc)) from exc


def enqueue_outputs(order_id: str) -> None:
    if not USE_CELERY:
        _run_outputs_inline(order_id)
        return
    try:
        celery_app.send_task("backend.src.workers.output_worker.generate_outputs", args=[order_id])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Output enqueue failed; running inline", order_id=order_id)
        try:
            _run_outputs_inline(order_id)
        except OutputBuildError:
            raise
        except Exception as inline_exc:  # noqa: BLE001
            raise OutputBuildError(str(inline_exc)) from inline_exc


@celery_app.task(name="backend.src.workers.output_worker.generate_outputs", bind=True, max_retries=3)
def generate_outputs(self, order_id: str):
    try:
        _run_outputs_inline(order_id)
    except OutputBuildError as exc:
        logger.exception("Output generation failed; retrying", order_id=order_id)
        if self.request.retries >= self.max_retries:
            order_service.set_status(order_id, "エラー")
        raise self.retry(exc=exc, countdown=10)
