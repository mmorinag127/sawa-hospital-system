"""Explicit expiry settlement, never task dispatch or business retry."""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import DBAPIError

from . import models
from .solve_delivery_dispatch_service import validate_contract
from .solve_delivery_service import (
    DeliveryBusy, DeliveryFenced, SolveClaim, _lock_row, classify_delivery_review, stop_expired_solve,
)


def _save_review(session, identity, now):
    delivery = _lock_row(session, models.SolveDelivery, identity)
    if (delivery is None or delivery.state != "claimed" or delivery.lease_until is None
            or delivery.lease_until > now):
        session.rollback()
        return False
    existing = session.query(models.SolveDeliveryReview).filter_by(delivery_id=identity).one_or_none()
    if existing is None:
        session.add(models.SolveDeliveryReview(delivery_id=identity,
            code="shift_recovery_requires_review", recorded_at=now))
    session.commit()
    return True


def recover_expired_deliveries(*, session_factory, config, now, limit):
    config.validate()
    if type(now) is not datetime or now.tzinfo is not None:
        raise ValueError("shift_recovery_explicit_utc_required")
    if type(limit) is not int or limit <= 0:
        raise ValueError("shift_recovery_explicit_limit_required")
    try:
        with session_factory() as session:
            highwater = session.execute(select(func.max(models.SolveDelivery.id))).scalar_one()
        items = []
        cursor, completed = 0, 0
        while highwater is not None and cursor < highwater and completed < limit:
            with session_factory() as session:
                identity = session.execute(select(models.SolveDelivery.id).where(
                    models.SolveDelivery.id > cursor, models.SolveDelivery.id <= highwater,
                    models.SolveDelivery.state == "claimed", models.SolveDelivery.lease_until <= now,
                    ~select(models.SolveDeliveryReview.id).where(
                        models.SolveDeliveryReview.delivery_id == models.SolveDelivery.id).exists(),
                ).order_by(models.SolveDelivery.id).limit(1)).scalar_one_or_none()
            if identity is None:
                break
            cursor = identity
            with session_factory() as session:
                try:
                    delivery = _lock_row(session, models.SolveDelivery, identity)
                    run = session.get(models.SolveRun, delivery.solve_run_id) if delivery is not None else None
                    if delivery is not None and classify_delivery_review(session, delivery, run, now=now) is not None:
                        session.rollback()
                        continue
                    if (delivery is None or delivery.state != "claimed"
                            or delivery.lease_until is None or delivery.lease_until > now):
                        session.rollback()
                        items.append({"delivery_id": identity, "state": "not_eligible", "warning": None})
                        continue
                    if (type(delivery.owner_attempt) is not str or not delivery.owner_attempt
                            or type(delivery.generation) is not int or delivery.generation <= 0
                            or delivery.receipt is not None):
                        raise DeliveryFenced("shift_recovery_invalid_ownership")
                    contract = delivery.transport_contract
                    if (validate_contract(contract) != config
                            or contract["run_id"] != delivery.solve_run_id
                            or contract["generation"] != delivery.generation):
                        raise DeliveryFenced("shift_recovery_contract_mismatch")
                    claim = SolveClaim(delivery.id, delivery.generation, delivery.owner_attempt)
                    # Immutable contract validation is observational. The core reacquires
                    # exact delivery/run NOWAIT locks and checks the saved input before CAS.
                    session.rollback()
                    run = stop_expired_solve(session, claim, now=now)
                    completed += 1
                    items.append({"delivery_id": identity, "state": "terminal",
                        "business_state": run.status, "warning": "shift_delivery_attempt_expired"})
                except DeliveryBusy:
                    session.rollback()
                    items.append({"delivery_id": identity, "state": "busy", "warning": "shift_recovery_busy"})
                except (DeliveryFenced, ValueError, KeyError, TypeError, AttributeError):
                    session.rollback()
                    try:
                        saved = _save_review(session, identity, now)
                    except DeliveryBusy:
                        session.rollback()
                        items.append({"delivery_id": identity, "state": "busy", "warning": "shift_recovery_busy"})
                        continue
                    if saved:
                        completed += 1
                        items.append({"delivery_id": identity, "state": "review", "warning": "shift_recovery_requires_review"})
                    else:
                        items.append({"delivery_id": identity, "state": "not_eligible", "warning": None})
        return {"items": items}
    except DBAPIError as error:
        raise HTTPException(503, "shift_recovery_database_unavailable") from error
