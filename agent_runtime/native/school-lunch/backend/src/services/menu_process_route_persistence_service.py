from copy import deepcopy
from datetime import timezone
import hashlib, json
from sqlalchemy import select, update
from fastapi import HTTPException
from src.models.menu_process_route import (
    ProcessRouteProfile,
    ProcessRouteProfileRevision,
    ProcessRouteArtifact,
)
from src.services.menu_process_route_service import load_template


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def timestamp(value):
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def profile_data(p, rev):
    return {
        "id": p.id,
        "facility_id": p.facility_id,
        "name": p.name,
        "version": p.version,
        "head_revision_id": p.head_revision_id,
        "confirmed_revision_id": p.confirmed_revision_id,
        "revision": revision_data(rev),
    }


def revision_data(r):
    return {
        "id": r.id,
        "number": r.number,
        "status": r.status,
        "template_id": r.template_id,
        "template_version": r.template_version,
        "document": r.document,
        "change_note": r.change_note,
        "created_at": timestamp(r.created_at),
    }


def artifact_data(a):
    return {
        "id": a.id,
        "menu_id": a.menu_execution_id,
        "process_revision_id": a.process_revision_id,
        "profile_revision_id": a.profile_revision_id,
        "renderer_version": a.renderer_version,
        "input_hash": a.input_hash,
        "svg": a.svg,
        "svg_hash": a.svg_hash,
        "topology": a.topology,
        "input_snapshot": a.input_snapshot,
        "generation_status": a.generation_status,
        "warnings": a.warnings,
        "created_at": timestamp(a.created_at),
    }


def save_profile(session, facility_id, body):
    profile = (
        session.get(ProcessRouteProfile, body.get("id")) if body.get("id") else None
    )
    if body.get("id") and not profile:
        raise HTTPException(404, "図面プロファイルが見つかりません")
    if profile and profile.facility_id != facility_id:
        raise HTTPException(404, "施設の図面プロファイルが見つかりません")
    if profile and profile.version != body["expected_version"]:
        raise HTTPException(409, "図面プロファイルが更新されました")
    if (
        body["status"] == "confirmed"
        and body["document"].get("routing_policy") == "candidate_geometry"
    ):
        raise HTTPException(422, "候補幾何経路は確認済みにできません")
    if not profile:
        if body["expected_version"] != 0:
            raise HTTPException(409, "図面プロファイルが更新されました")
        profile = ProcessRouteProfile(
            facility_id=facility_id, name=body["name"], version=0
        )
        session.add(profile)
        session.flush()
    svg, manifest = load_template(body["template_id"])
    network = manifest.pop("flow_network", {})
    number = profile.version + 1
    snapshot_hashes = {
        "svg": hashlib.sha256(svg).hexdigest(),
        "manifest": digest(manifest),
        "network": digest(network),
    }
    # Reserve the revision under CAS before its unique (profile,number) insert.
    # A concurrent editor must receive 409, not a database uniqueness 500.
    if (
        session.execute(
            update(ProcessRouteProfile)
            .where(
                ProcessRouteProfile.id == profile.id,
                ProcessRouteProfile.version == body["expected_version"],
            )
            .values(version=number)
        ).rowcount
        != 1
    ):
        raise HTTPException(409, "図面プロファイルが更新されました")
    rev = ProcessRouteProfileRevision(
        profile_id=profile.id,
        number=number,
        parent_revision_id=profile.head_revision_id,
        status=body["status"],
        template_id=body["template_id"],
        template_version=manifest["version"],
        svg_snapshot=svg.decode(),
        manifest_snapshot=manifest,
        network_snapshot=network,
        snapshot_hashes=snapshot_hashes,
        document=deepcopy(body["document"]),
        metadata_json={},
        change_note=body.get("change_note", ""),
    )
    session.add(rev)
    session.flush()
    values = {"name": body["name"], "version": number, "head_revision_id": rev.id}
    if body["status"] == "confirmed":
        values["confirmed_revision_id"] = rev.id
    session.execute(
        update(ProcessRouteProfile)
        .where(ProcessRouteProfile.id == profile.id)
        .values(**values)
    )
    session.refresh(profile)
    session.flush()
    return profile_data(profile, rev)


def profiles(session, facility_id):
    return [
        profile_data(p, session.get(ProcessRouteProfileRevision, p.head_revision_id))
        for p in session.scalars(
            select(ProcessRouteProfile).where(
                ProcessRouteProfile.facility_id == facility_id
            )
        ).all()
    ]


def generate(session, menu, revision, profile_revision_id):
    profile = session.get(ProcessRouteProfileRevision, profile_revision_id)
    if not profile:
        raise HTTPException(404, "図面プロファイル改訂が見つかりません")
    owner = session.get(ProcessRouteProfile, profile.profile_id)
    if owner.facility_id != menu.facility_id:
        raise HTTPException(422, "図面プロファイルの施設が一致しません")
    from src.services import menu_process_route_service as engine

    document = {
        **deepcopy(profile.document),
        "svg_snapshot": profile.svg_snapshot,
        "manifest_snapshot": profile.manifest_snapshot,
        "network_snapshot": profile.network_snapshot,
    }
    result = engine.generate(revision, menu, document)
    status = 'draft' if profile.status != 'confirmed' or result['generation_status'] == 'draft' else 'confirmed'
    warnings = [] if status == "confirmed" else ["未確認の図面経路・現場確認前"]
    snapshot = {
        "process_document": revision.document,
        "process_source_snapshot": revision.source_snapshot,
        "profile": document,
        "menu_id": menu.id,
        "renderer_version": engine.RENDERER_VERSION,
    }
    svg = result["svg"]
    a = ProcessRouteArtifact(
        menu_execution_id=menu.id,
        process_revision_id=revision.id,
        profile_revision_id=profile.id,
        renderer_version=engine.RENDERER_VERSION,
        input_hash=digest(snapshot),
        svg=svg,
        svg_hash=hashlib.sha256(svg.encode()).hexdigest(),
        topology=result.get("topology", []),
        input_snapshot=snapshot,
        generation_status=status,
        warnings=warnings,
    )
    session.add(a)
    session.flush()
    return artifact_data(a)
