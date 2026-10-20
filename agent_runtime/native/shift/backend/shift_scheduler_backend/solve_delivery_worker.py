"""Authenticated internal callable. No public route or API security exception."""

import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from .models import SolveDelivery, SolveRun
from .solve_delivery_auth import require_identity
from .solve_delivery_dispatch_service import validate_contract
from .solve_delivery_service import (
    DeliveryBusy, _lock_row, claim_solve, classify_delivery_review, execute_claimed_solve,
)


def process_delivery(body, authorization, *, config, session_factory, clock, lease_until, verifier):
    require_identity(authorization, config=config, verifier=verifier)
    now = clock()
    if (type(now) is not datetime or type(lease_until) is not datetime
            or now.tzinfo is not None or lease_until.tzinfo is not None
            or lease_until - now < timedelta(seconds=config.deadline_seconds)):
        raise HTTPException(400, "shift_task_explicit_lease_deadline_mismatch")
    try:
        identity = json.loads(body)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "shift_task_body_invalid") from error
    if (type(identity) is not dict or set(identity) != {"run_id", "generation"}
            or any(type(value) is not int or value <= 0 for value in identity.values())):
        raise HTTPException(400, "shift_task_body_invalid")
    with session_factory() as session:
        delivery = session.execute(select(SolveDelivery).where(
            SolveDelivery.solve_run_id == identity["run_id"])).scalar_one_or_none()
        if delivery is None or delivery.generation != identity["generation"]:
            raise HTTPException(409, "shift_task_identity_differs")
        try:
            delivery = _lock_row(session, SolveDelivery, delivery.id)
        except DeliveryBusy as error:
            raise HTTPException(503, "shift_delivery_busy") from error
        if (delivery is None or delivery.solve_run_id != identity["run_id"]
                or delivery.generation != identity["generation"]):
            raise HTTPException(409, "shift_task_identity_differs")
        try:
            captured = validate_contract(delivery.transport_contract)
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(409, "shift_task_contract_differs") from error
        if (captured != config or delivery.transport_contract["run_id"] != identity["run_id"]
                or delivery.transport_contract["generation"] != identity["generation"]):
            raise HTTPException(409, "shift_task_contract_differs")
        if delivery.state == "terminal" and delivery.receipt is not None:
            receipt = delivery.receipt
            if (type(receipt) is not dict or receipt.get("run_id") != identity["run_id"]
                    or receipt.get("generation") != identity["generation"]
                    or receipt.get("input_version") != delivery.input_version
                    or receipt.get("owner_attempt") != delivery.owner_attempt):
                raise HTTPException(409, "shift_task_terminal_receipt_invalid")
            return {"state": "terminal", "receipt": delivery.receipt}
        try:
            run = _lock_row(session, SolveRun, delivery.solve_run_id)
        except DeliveryBusy as error:
            raise HTTPException(503, "shift_delivery_busy") from error
        review = classify_delivery_review(session, delivery, run, now=now)
        if review is not None:
            if review.code != "shift_recovery_requires_review":
                raise HTTPException(409, "shift_task_review_invalid")
            if review.active_lease:
                raise HTTPException(503, "shift_delivery_busy")
            if not review.stopped_for_review:
                raise HTTPException(409, "shift_task_review_not_acknowledgeable")
            session.rollback()
            return {"state": "review", "warning": review.code}
        delivery_id = delivery.id
        session.rollback()
        try:
            claim_now = clock()
            if lease_until - claim_now < timedelta(seconds=config.deadline_seconds):
                raise HTTPException(400, "shift_task_explicit_lease_deadline_mismatch")
            claim = claim_solve(session, delivery_id, now=claim_now, lease_until=lease_until)
        except DeliveryBusy as error:
            raise HTTPException(503, "shift_delivery_busy") from error
        if claim is None:
            raise HTTPException(503, "shift_delivery_not_claimable")
        execute_claimed_solve(session, claim, clock=clock)
        settled = session.get(SolveDelivery, delivery_id)
        return {"state": settled.state, "receipt": settled.receipt}
