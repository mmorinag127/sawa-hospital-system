from src.workers import celery_app


class OutputBuildError(RuntimeError):
    pass


class OriginalOutputExecutionRequired(OutputBuildError):
    pass


def _reject_unrecorded_execution() -> None:
    raise OriginalOutputExecutionRequired(
        "Legacy order-id-only output execution is disabled: recorded original execution version is required. "
        "No output was generated, no order was changed, and no retry was scheduled."
    )


def enqueue_outputs(order_id: str) -> None:
    _reject_unrecorded_execution()


@celery_app.task(name="backend.src.workers.output_worker.generate_outputs", bind=True, max_retries=0, autoretry_for=())
def generate_outputs(self, order_id: str):
    _reject_unrecorded_execution()
