"""Explicit initial adoption of saved results, not another OCR execution."""
from copy import deepcopy

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_store as store
from src.services.canonical_facility_lineage import digest

IMPORTS = "initial_saved_imports"
CONFIGURATIONS = "initial_saved_configurations"
TRANSPORT = "_initial_saved_import_receipt"
OWNED = "initial_saved_import_owned"
RETIRED = "_initial_saved_import_retired"


def _transaction(session):
    return session.get_nested_transaction() or session.get_transaction()


def _owner(order_id):
    from src.services.order_workflow_v2_service import _workflow_command_identity
    from src.services.workflow_attempt_guard_service import current_workflow_execution
    command = _workflow_command_identity.get()
    if command is not None and command["order_id"] == order_id:
        return command
    execution = current_workflow_execution()
    if (isinstance(execution, dict) and execution.get("order_id") == order_id
            and execution.get("job_id") and execution.get("attempt")
            and type(execution.get("revision")) is int):
        return {key: execution[key] for key in ("job_id", "order_id", "attempt", "revision")}
    return None


def _owned(session, order_id):
    record = session.info.get(OWNED, {}).get(order_id)
    if record is None or record["owner"] != _owner(order_id):
        return None
    transaction = _transaction(session)
    while transaction is not None:
        if record["transaction"] is transaction:
            return record
        transaction = transaction.parent
    return None


def _canonical_selection(session, template_version_id):
    from src.services import canonical_facility_lineage as lineage
    row = lineage.read_projection(session, template_version_id)
    if row is not None and row.source == lineage.SOURCE:
        lineage.verify_projection(row)
        return True
    return False


def prepare_import_master(master, snapshots, policy):
    from src.services.initial_saved_source_service import source_receipt

    if not isinstance(policy, dict):
        raise ValueError("initial_saved_import_policy_required")
    result = deepcopy(master)
    if IMPORTS in result or CONFIGURATIONS in result:
        raise ValueError("initial_saved_import_already_prepared")
    result[IMPORTS], result[CONFIGURATIONS] = {}, {}
    for snapshot in snapshots:
        order = snapshot["order"]
        versions = store.facility_versions(master, order["facility_code"])
        initial = [version for version in versions if "initial_snapshot" in version]
        if len(initial) != 1:
            raise ValueError("initial_saved_import_configuration_unresolved")
        configuration = {"version": deepcopy(initial[0]), "policy": deepcopy(policy)}
        configuration_id = digest(configuration)
        result[CONFIGURATIONS][configuration_id] = configuration
        receipt = source_receipt(snapshot)
        if order["id"] in result[IMPORTS]:
            raise ValueError("initial_saved_import_duplicate_order")
        result[IMPORTS][order["id"]] = {"receipt": receipt, "configuration_id": configuration_id}
    store._encode(result)
    return result


def get_binding(session, order_id, *, template_version_id=None):
    from src.services import facility_master_service, config_service
    from src.services.initial_saved_source_service import validate_saved_source, capture_saved_source

    master = facility_master_service.get_snapshot().master
    imports = master.get(IMPORTS)
    if imports is None:
        return None
    if not isinstance(imports, dict):
        raise ValueError("initial_saved_import_invalid")
    record = imports.get(order_id)
    if record is None:
        return None
    with session.no_autoflush:
        table = Order.__table__
        order = session.execute(select(*table.c).where(table.c.id == order_id)).mappings().one_or_none()
    if order is None:
        raise ValueError("initial_saved_source_changed")
    if template_version_id is not None and template_version_id != order["template_version_id"]:
        raise ValueError("initial_saved_source_changed")
    if _canonical_selection(session, order["template_version_id"]):
        return None
    if not isinstance(record, dict) or set(record) != {"receipt", "configuration_id"}:
        raise ValueError("initial_saved_import_invalid")
    configurations = master.get(CONFIGURATIONS)
    if not isinstance(configurations, dict):
        raise ValueError("initial_saved_import_configuration_unresolved")
    configuration = configurations.get(record["configuration_id"])
    if not isinstance(configuration, dict) or digest(configuration) != record["configuration_id"]:
        raise ValueError("initial_saved_import_configuration_changed")
    facility = config_service.validate_selected_authored_facility_config(
        order["facility_code"], configuration["version"], master=master,
    )
    with session.no_autoflush:
        table = OrderWorkflowState.__table__
        workflow = session.execute(select(*table.c).where(table.c.order_id == order_id)).mappings().one_or_none()
    if workflow is None:
        raise ValueError("initial_saved_source_changed")
    actions = workflow["secondary_actions_json"]
    transport = actions.get(TRANSPORT) if isinstance(actions, dict) else None
    if isinstance(actions, dict) and RETIRED in actions:
        raise ValueError("initial_saved_import_retired")
    root = record["receipt"]
    receipt = root
    if transport is not None:
        if not isinstance(transport, dict) or transport.get("root_source_sha256") != root.get("source_sha256"):
            raise ValueError("initial_saved_source_changed")
        receipt = transport.get("receipt")
    owned = _owned(session, order_id)
    if owned is not None:
        if owned["record"] != record:
            raise ValueError("initial_saved_import_configuration_changed")
        snapshot = capture_saved_source(session, order_id)
    else:
        snapshot = validate_saved_source(session, receipt)
    if root.get("order_id") != order_id:
        raise ValueError("initial_saved_source_changed")
    binding = {"record": deepcopy(record), "snapshot": snapshot,
               "configuration": deepcopy(configuration), "facility": facility}
    return binding


def execution_input(session, binding):
    from src.models.document import OrderDocument
    from src.models.order_critical_decision import OrderCriticalDecision
    from src.models.order_ocr_cache import OrderOcrCache
    from src.services.initial_saved_source_service import source_receipt
    from src.services.workflow_attempt_guard_service import _json_snapshot

    snapshot = binding["snapshot"]
    order = snapshot["order"]
    def read(model, key, identity):
        if identity is None:
            return None
        table = model.__table__
        row = session.execute(select(*table.c).where(table.c[key] == identity)).mappings().one_or_none()
        return dict(row) if row is not None else None
    with session.no_autoflush:
        decisions = OrderCriticalDecision.__table__
        decision_rows = [dict(row) for row in session.execute(select(*decisions.c).where(
            decisions.c.order_id == order["id"],
        ).order_by(decisions.c.id)).mappings()]
        cache = read(OrderOcrCache, "order_id", order["id"])
        document = read(OrderDocument, "id", order["current_document_id"])
    receipt = source_receipt(snapshot)
    return _json_snapshot({
        "order_exists": True, "workflow_exists": True,
        "workflow_revision": snapshot["workflow"]["revision"],
        "order": order, "workflow": snapshot["workflow"], "draft": snapshot["draft"],
        "evidence": snapshot["evidence"], "lines": snapshot["lines"],
        "cache": cache, "document": document, "decisions": decision_rows,
        "original_template_projection": snapshot["template"],
        "original_template_lineage": receipt, "original_template_source_proof": receipt,
        "initial_saved_import": deepcopy(binding["record"]),
        "configured_input": {"facility": binding["facility"], "policy": binding["configuration"]["policy"]},
        "physical_menu_entries": [],
    })


def processing_input(session, binding):
    """Capture current monthly rows for a new operation, never for saved reads."""
    from src.services.order_service import _build_position_menu_entries_safe
    from src.services.initial_saved_source_service import source_receipt
    from src.services.workflow_attempt_guard_service import _json_snapshot
    if binding["snapshot"]["evidence"] is None:
        raise ValueError("selected_ocr_required")
    captured = execution_input(session, binding)
    order = binding["snapshot"]["order"]
    entries = _build_position_menu_entries_safe(order["week_code"], order["facility_code"], session=session)
    if not entries:
        raise ValueError("menu_entries_missing")
    captured["physical_menu_entries"] = _json_snapshot(entries)
    captured["initial_saved_source_receipt"] = source_receipt(binding["snapshot"])
    return captured


def processing_configuration_matches(session, order_id, captured):
    binding = get_binding(session, order_id)
    return (binding is not None
            and captured.get("initial_saved_import") == binding["record"]
            and captured.get("configured_input") == {
                "facility": binding["facility"], "policy": binding["configuration"]["policy"]})


def capture_queued_processing_input(session, order_id, captured):
    if captured.get("initial_saved_import") is None:
        return captured
    if _owned(session, order_id) is None:
        raise ValueError("initial_saved_import_ownership_rejected")
    binding = get_binding(session, order_id)
    if binding is None:
        raise ValueError("initial_saved_source_changed")
    queued = processing_input(session, binding)
    if set(queued) != set(captured):
        raise ValueError("initial_saved_ocr_candidate_source_changed")
    # Import receipts include the owned workflow revision as well as saved data.
    queue_envelope = {"workflow", "workflow_revision", "initial_saved_source_receipt",
                      "original_template_lineage", "original_template_source_proof"}
    for key, value in captured.items():
        if key not in queue_envelope and queued.get(key) != value:
            raise ValueError("initial_saved_ocr_candidate_source_changed")
    return queued


def materialization_input_matches(session, order_id, captured):
    binding = get_binding(session, order_id)
    if binding is None or not processing_configuration_matches(session, order_id, captured):
        return False
    snapshot = binding["snapshot"]
    versions = captured.get("input_versions")
    return (snapshot["evidence"] is not None and isinstance(versions, dict)
            and captured.get("draft_id") == snapshot["draft"]["id"]
            and captured.get("evidence_run_id") == snapshot["evidence"]["id"]
            and versions.get("draft") == snapshot["draft"]
            and versions.get("evidence") == snapshot["evidence"]
            and versions.get("order_lines") == snapshot["lines"])


def prepare_new_ocr_selection(session, order, workflow):
    """Pin the saved configuration for a candidate without adopting a new lineage."""
    binding = get_binding(session, order.id)
    if binding is None:
        return
    if _owned(session, order.id) is None:
        raise ValueError("initial_saved_import_ownership_rejected")
    from src.services.order_service import _parse_sheet_week_value
    _, start, end = _parse_sheet_week_value(order.week_code)
    if start is None or end is None:
        raise ValueError("materialization_week_input_mismatch")
    template = binding["snapshot"]["template"]
    if template is None:
        raise ValueError("template_version_required")
    actions = deepcopy(workflow.secondary_actions_json)
    meta = actions.setdefault("workflow_v2", {})
    meta.update({"facility_id": order.facility_code, "week_start": start.isoformat(),
                 "week_end": end.isoformat(), "template_version_id": template["id"],
                 "template_version_digest": template["template_digest"]})
    workflow.secondary_actions_json = actions
    session.flush()


def sheet_context(binding):
    from src.services.initial_saved_source_service import current_sheet_payload

    snapshot = binding["snapshot"]
    payload = current_sheet_payload(snapshot)
    draft, order = snapshot["draft"], snapshot["order"]
    return {
        **deepcopy(payload), "order_id": order["id"], "facility_id": order["facility_code"],
        "draft_id": draft["id"], "template_version_id": draft["template_version_id"],
        "base_evidence_run_id": draft["base_evidence_run_id"],
        "draft_record": deepcopy(draft), "source_draft_record": deepcopy(draft),
        "draft_payload": payload, "has_persisted_draft": True,
        "authoritative_persisted_draft": True, "clean_saved_draft": not draft["blockers_json"],
        "resolved_week_id": order["week_code"],
    }


def output_candidate(binding):
    from src.services.initial_saved_source_service import accepted_output_payload

    saved = accepted_output_payload(binding["snapshot"])
    candidate = deepcopy(saved["materialization_candidate"])
    if not isinstance(candidate.get("lines"), list):
        raise ValueError("initial_saved_source_output_invalid")
    if "output_lines" not in candidate:
        candidate["output_lines"] = deepcopy(candidate["lines"])
    elif not isinstance(candidate["output_lines"], list):
        raise ValueError("initial_saved_source_output_invalid")
    candidate["configured_input"] = {"facility": deepcopy(binding["facility"]),
                                     "policy": deepcopy(binding["configuration"]["policy"])}
    candidate["initial_saved_import"] = deepcopy(binding["record"])
    candidate["initial_saved_bag_rows"] = saved["bag_rows"]
    return candidate


def begin_owned_import(session, order_id, *, expected_revision):
    from src.services.workflow_attempt_guard_service import lock_order_execution_input, lock_current_execution_write
    owner = _owner(order_id)
    if owner is None or type(expected_revision) is not int:
        raise ValueError("initial_saved_import_command_required")
    if "command" in owner:
        from src.services.order_workflow_v2_service import _expected_workflow_revision
        declared_revision = _expected_workflow_revision.get()
    else:
        declared_revision = owner["revision"]
    if expected_revision != declared_revision:
        raise ValueError("initial_saved_import_ownership_rejected")
    existing = _owned(session, order_id)
    if existing is not None:
        return
    with session.no_autoflush:
        if lock_order_execution_input(session, order_id) is None or not lock_current_execution_write(session, order_id):
            raise ValueError("initial_saved_import_ownership_rejected")
        table = OrderWorkflowState.__table__
        actual = session.execute(select(table.c.revision).where(table.c.order_id == order_id)).scalar_one_or_none()
        if actual != expected_revision:
            raise ValueError("initial_saved_import_ownership_rejected")
        binding = get_binding(session, order_id)
    if binding is not None:
        from src.services.initial_saved_source_service import source_receipt
        session.info.setdefault(OWNED, {})[order_id] = {
            "transaction": _transaction(session), "owner": deepcopy(owner),
            "record": deepcopy(binding["record"]), "revision": actual, "finished": False,
            "begin_source_sha256": source_receipt(binding["snapshot"])["source_sha256"],
            "begin_transport": deepcopy((binding["snapshot"]["workflow"]["secondary_actions_json"] or {}).get(TRANSPORT)),
        }


def finish_owned_import(session, order_id, *, expected_revision=None):
    from src.services.initial_saved_source_service import capture_saved_source, source_receipt
    owned = _owned(session, order_id)
    if owned is None:
        return
    session.flush()
    table = OrderWorkflowState.__table__
    workflow = dict(session.execute(select(*table.c).where(table.c.order_id == order_id)).mappings().one())
    if (type(expected_revision) is not int or workflow["revision"] != expected_revision
            or expected_revision <= owned.get("finished_revision", owned["revision"])):
        raise ValueError("initial_saved_import_ownership_rejected")
    order_table = Order.__table__
    template_id = session.execute(select(order_table.c.template_version_id).where(order_table.c.id == order_id)).scalar_one()
    actions = deepcopy(workflow["secondary_actions_json"]) if isinstance(workflow["secondary_actions_json"], dict) else {}
    if _canonical_selection(session, template_id):
        actions.pop(TRANSPORT, None)
        actions[RETIRED] = owned["record"]["receipt"]["source_sha256"]
    else:
        snapshot = capture_saved_source(session, order_id)
        actions[TRANSPORT] = {"root_source_sha256": owned["record"]["receipt"]["source_sha256"],
                              "receipt": source_receipt(snapshot)}
    result = session.execute(update(table).where(table.c.order_id == order_id,
        table.c.revision == workflow["revision"]).values(secondary_actions_json=actions))
    if result.rowcount != 1:
        raise ValueError("initial_saved_import_ownership_rejected")
    # Keep the already loaded workflow coherent without refreshing dirty caller objects.
    from sqlalchemy.orm.attributes import set_committed_value
    for row in session.identity_map.values():
        if isinstance(row, OrderWorkflowState) and row.order_id == order_id:
            set_committed_value(row, "secondary_actions_json", deepcopy(actions))
    owned["finished"] = True
    owned["finished_revision"] = expected_revision
    if RETIRED in actions:
        owned["retired_sha256"] = _retired_state_digest(session, order_id)
    else:
        owned["transport"] = deepcopy(actions[TRANSPORT])


def _retired_state_digest(session, order_id):
    """Transaction-only fence, not an imported source or approval receipt."""
    from src.services.initial_saved_source_service import (
        OrderLine, OrderSheetDraft, OrderOcrEvidenceRun, FacilityTemplateVersion,
        OrderConfirmedSnapshot, OrderBaggingResult, OrderOutputBundle, _json_safe,
    )
    def read(model, key, identity):
        if identity is None:
            return None
        table = model.__table__
        row = session.execute(select(*table.c).where(table.c[key] == identity)).mappings().one_or_none()
        if row is None:
            raise ValueError("initial_saved_import_retired_source_changed")
        return dict(row)
    with session.no_autoflush:
        order = read(Order, "id", order_id)
        workflow = read(OrderWorkflowState, "order_id", order_id)
        actions = deepcopy(workflow["secondary_actions_json"])
        actions.pop(TRANSPORT, None)
        workflow["secondary_actions_json"] = actions
        meta = actions.get("workflow_v2", {})
        table = OrderLine.__table__
        values = {"order": order, "workflow": workflow,
            "draft": read(OrderSheetDraft, "id", workflow["draft_id"]),
            "evidence": read(OrderOcrEvidenceRun, "id", workflow["evidence_run_id"]),
            "template": read(FacilityTemplateVersion, "id", order["template_version_id"]),
            "confirmed": read(OrderConfirmedSnapshot, "id", workflow["confirmed_snapshot_id"]),
            "bagging": read(OrderBaggingResult, "id", meta.get("bagging_result_id")),
            "output": read(OrderOutputBundle, "id", meta.get("output_bundle_id")),
            "lines": [dict(row) for row in session.execute(select(*table.c).where(table.c.order_id == order_id).order_by(table.c.id)).mappings()]}
    return digest(_json_safe(values))


@event.listens_for(Session, "before_commit")
def _validate_finished_imports(session):
    if not session.info.get(OWNED) or session.get_nested_transaction() is not None:
        return
    session.flush()
    from src.services.initial_saved_source_service import validate_saved_source, capture_saved_source, source_receipt
    for order_id, owned in tuple(session.info.get(OWNED, {}).items()):
        if not owned["finished"]:
            try:
                snapshot = capture_saved_source(session, order_id)
                unchanged = (source_receipt(snapshot)["source_sha256"] == owned["begin_source_sha256"]
                             and (snapshot["workflow"]["secondary_actions_json"] or {}).get(TRANSPORT) == owned["begin_transport"])
            except ValueError:
                unchanged = False
            if not unchanged:
                raise ValueError("initial_saved_import_unfinished")
            continue
        table = OrderWorkflowState.__table__
        actions = session.execute(select(table.c.secondary_actions_json).where(table.c.order_id == order_id)).scalar_one()
        if RETIRED not in actions:
            if actions.get(TRANSPORT) != owned.get("transport"):
                raise ValueError("initial_saved_source_changed")
            validate_saved_source(session, actions[TRANSPORT]["receipt"])
        elif _retired_state_digest(session, order_id) != owned.get("retired_sha256"):
            raise ValueError("initial_saved_import_retired_source_changed")


@event.listens_for(Session, "after_transaction_end")
def _clear_owned_imports(session, transaction):
    if transaction.parent is None:
        session.info.pop(OWNED, None)
    elif transaction.nested:
        for owned in session.info.get(OWNED, {}).values():
            if owned["transaction"] is transaction:
                owned["transaction"] = transaction.parent


@event.listens_for(Session, "after_rollback")
def _rollback_owned_imports(session):
    session.info.pop(OWNED, None)
