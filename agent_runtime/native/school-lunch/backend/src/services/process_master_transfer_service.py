"""Explicit, reviewable transfer of reusable school-lunch masters.

The bundle intentionally has no menu-execution, day-bound revision, proposal,
or generated-artifact row. Source row IDs are identities for comparison only;
new target rows receive target-local IDs.
"""

from copy import deepcopy
import hashlib
import json

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from src.models.menu_process import ProcessRecipe, ProcessRevision
from src.models.menu_process_route import (
    ProcessRouteProfile,
    ProcessRouteProfileRevision,
)
from src.models.school_lunch import Facility
from src.schemas.menu_process import ProcessDocument
from src.services.named_svg_route_service import validate_pair
from src.services.menu_process_service import lock_recipes, mutation_operation


FORMAT = "sawa-lunch-master-transfer-v1"


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def bad(message, status=422):
    raise HTTPException(status, message)


def normalized_document(raw):
    try:
        document = ProcessDocument.model_validate(raw)
        document.check_confirmable()
    except (ValidationError, ValueError) as exc:
        bad(f"確認済み工程の形式が不正です: {exc}")
    data = document.model_dump()
    mapping = {
        row["id"]: f"m{index}" for index, row in enumerate(data["ingredients"], 1)
    }
    for row in data["ingredients"]:
        row["id"] = mapping[row["id"]]
    for step in data["steps"]:
        for source in step["inputs"]:
            if source["ingredient_id"]:
                source["ingredient_id"] = mapping[source["ingredient_id"]]
        for output in step.get('outputs', []):
            if output.get('ingredient_ids') is not None:
                output['ingredient_ids'] = [mapping[mid] for mid in output['ingredient_ids']]
    return data


def export_bundle(session, facility_id, recipe_revision_ids, profile_revision_ids):
    facility = session.get(Facility, facility_id)
    if not facility:
        bad("施設が見つかりません", 404)
    if not recipe_revision_ids and not profile_revision_ids:
        bad("移行する設定を選択してください")
    if len(recipe_revision_ids) != len(set(recipe_revision_ids)) or len(
        profile_revision_ids
    ) != len(set(profile_revision_ids)):
        bad("同じ設定を重複して選択できません")
    items = []
    for rid in sorted(recipe_revision_ids):
        rev = session.get(ProcessRevision, rid)
        recipe = session.get(ProcessRecipe, rev.recipe_id) if rev else None
        if (
            not recipe
            or recipe.facility_id != facility_id
            or recipe.confirmed_revision_id != rid
            or rev.scope != "menu"
            or rev.status != "confirmed"
        ):
            bad(f"工程マスター改訂 {rid} は選択できません")
        item = {
            "kind": "recipe",
            "source_key": f"stg:{facility_id}:recipe:{recipe.id}",
            "source_revision": rev.number,
            "name": recipe.name,
            "document": normalized_document(rev.document),
        }
        item["source_hash"] = digest(item)
        items.append(item)
    for rid in sorted(profile_revision_ids):
        rev = session.get(ProcessRouteProfileRevision, rid)
        profile = session.get(ProcessRouteProfile, rev.profile_id) if rev else None
        if (
            not profile
            or profile.facility_id != facility_id
            or profile.head_revision_id != rid
        ):
            bad(f"施設図面改訂 {rid} は選択できません")
        validate_pair(rev.svg_snapshot.encode(), rev.manifest_snapshot)
        item = {
            "kind": "profile",
            "source_key": f"stg:{facility_id}:profile:{profile.id}",
            "source_revision": rev.number,
            "name": profile.name,
            "status": rev.status,
            "template_id": rev.template_id,
            "template_version": rev.template_version,
            "document": deepcopy(rev.document),
            "svg_snapshot": rev.svg_snapshot,
            "manifest_snapshot": deepcopy(rev.manifest_snapshot),
            "network_snapshot": deepcopy(rev.network_snapshot),
            "snapshot_hashes": deepcopy(rev.snapshot_hashes),
        }
        item["source_hash"] = digest(item)
        items.append(item)
    bundle = {
        "format": FORMAT,
        "source_environment": "stg",
        "source_facility": {"id": facility.id, "name": facility.name},
        "items": items,
    }
    bundle["bundle_hash"] = digest(bundle)
    return bundle


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {
        "format",
        "source_environment",
        "source_facility",
        "items",
        "bundle_hash",
    }:
        bad("移行ファイルの形式が違います")
    if (
        bundle["format"] != FORMAT
        or bundle["source_environment"] != "stg"
        or digest({k: v for k, v in bundle.items() if k != "bundle_hash"})
        != bundle["bundle_hash"]
    ):
        bad("移行ファイルの版またはハッシュが一致しません")
    if (
        not isinstance(bundle["items"], list)
        or not bundle["items"]
        or len(bundle["items"]) > 100
    ):
        bad("移行する設定の件数が不正です")
    if not isinstance(bundle["source_facility"], dict) or set(
        bundle["source_facility"]
    ) != {"id", "name"}:
        bad("移行元施設が不正です")
    keys = set()
    for item in bundle["items"]:
        if not isinstance(item, dict) or item.get("kind") not in {"recipe", "profile"}:
            bad("設定の種類が不正です")
        key = item.get("source_key")
        expected_key = f"stg:{bundle['source_facility']['id']}:{item['kind']}:"
        if not isinstance(key, str) or not key.startswith(expected_key) or key in keys:
            bad("移行元設定の識別子が不正です")
        keys.add(key)
        if item.get("source_hash") != digest(
            {k: v for k, v in item.items() if k != "source_hash"}
        ):
            bad("設定のハッシュが一致しません")
        if item["kind"] == "recipe":
            if set(item) != {
                "kind",
                "source_key",
                "source_revision",
                "name",
                "document",
                "source_hash",
            }:
                bad("工程マスターの項目が不正です")
            if normalized_document(item["document"]) != item["document"]:
                bad("工程マスターの材料IDが可搬形式ではありません")
        else:
            if set(item) != {
                "kind",
                "source_key",
                "source_revision",
                "name",
                "status",
                "template_id",
                "template_version",
                "document",
                "svg_snapshot",
                "manifest_snapshot",
                "network_snapshot",
                "snapshot_hashes",
                "source_hash",
            }:
                bad("施設図面の項目が不正です")
            if (
                not isinstance(item["manifest_snapshot"], dict)
                or not isinstance(item["svg_snapshot"], str)
                or not isinstance(item["snapshot_hashes"], dict)
            ):
                bad("施設図面のデータ形式が不正です")
            if (
                item["status"] not in ("draft", "confirmed")
                or item["template_id"] != item["manifest_snapshot"].get("template_id")
                or item["template_version"] != item["manifest_snapshot"].get("version")
            ):
                bad("施設図面の版が一致しません")
            try:
                validate_pair(item["svg_snapshot"].encode(), item["manifest_snapshot"])
            except (ValueError, TypeError) as exc:
                bad(f"施設図面のSVGと設備設定が一致しません: {exc}")
            checks = item["snapshot_hashes"]
            if checks != {
                "svg": hashlib.sha256(item["svg_snapshot"].encode()).hexdigest(),
                "manifest": hashlib.sha256(
                    json.dumps(
                        item["manifest_snapshot"], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest(),
                "network": hashlib.sha256(
                    json.dumps(
                        item["network_snapshot"], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest(),
            }:
                bad("施設図面のスナップショットが一致しません")
            if (
                item["status"] == "confirmed"
                and item["document"].get("routing_policy") == "candidate_geometry"
            ):
                bad("候補経路を確認済みの図面として移行できません")


def _target(session, facility_id, item, selected_id=None):
    model = ProcessRecipe if item["kind"] == "recipe" else ProcessRouteProfile
    matches = session.scalars(
        select(model).where(
            model.facility_id == facility_id,
            model.transfer_source_key == item["source_key"],
        ).limit(2).execution_options(populate_existing=True)
    ).all()
    if len(matches) > 1:
        bad("同じ施設・移行元キーの設定が重複しています。自動選択できません", 409)
    existing = matches[0] if matches else None
    if selected_id is not None:
        chosen = session.get(model, selected_id, populate_existing=True)
        if (
            not chosen
            or chosen.facility_id != facility_id
            or (
                chosen.transfer_source_key
                and chosen.transfer_source_key != item["source_key"]
            )
        ):
            bad("指定した移行先設定が施設または移行元と一致しません")
        if existing and existing.id != chosen.id:
            bad("移行元は別の設定に登録済みです", 409)
        existing = chosen
    return existing


def _current_document(session, item, target):
    if not target:
        return None
    revision = session.get(
        ProcessRevision if item["kind"] == "recipe" else ProcessRouteProfileRevision,
        target.head_revision_id,
    )
    if not revision:
        bad("移行先の現在版が見つかりません", 409)
    return {
        "name": target.name,
        "status": revision.status,
        "document": revision.document,
        "template_id": getattr(revision, "template_id", None),
        "snapshot_hashes": getattr(revision, "snapshot_hashes", None),
    }


def preview(session, bundle, target_facility_id, choices):
    validate_bundle(bundle)
    facility = session.get(Facility, target_facility_id)
    if not facility or facility.name != bundle["source_facility"]["name"]:
        bad("移行先の施設名が一致しません。施設を明示して選択してください", 409)
    if (
        not isinstance(choices, list)
        or any(
            not isinstance(c, dict) or not isinstance(c.get("source_key"), str)
            for c in choices
        )
        or len({c["source_key"] for c in choices}) != len(choices)
    ):
        bad("移行する設定の選択が不正です")
    choice_map = {c["source_key"]: c for c in choices}
    if set(choice_map) - {i["source_key"] for i in bundle["items"]}:
        bad("移行ファイルにない設定が選択されています")
    plans = []
    for item in bundle["items"]:
        choice = choice_map.get(
            item["source_key"], {"source_key": item["source_key"], "action": "skip"}
        )
        if set(choice) - {"source_key", "action", "target_id"} or choice.get(
            "action"
        ) not in {"skip", "create", "append"}:
            bad("移行方法が不正です")
        target = _target(session, facility.id, item, choice.get("target_id"))
        model = ProcessRecipe if item["kind"] == "recipe" else ProcessRouteProfile
        name_conflicts = (
            session.scalars(
                select(model).where(
                    model.facility_id == facility.id, model.name == item["name"]
                )
            ).all()
            if not target
            else []
        )
        current = _current_document(session, item, target)
        source_summary = {
            "name": item["name"],
            "status": item.get("status", "confirmed"),
            "document": item["document"],
            "template_id": item.get("template_id"),
            "snapshot_hashes": item.get("snapshot_hashes"),
        }
        state = (
            "identical"
            if target and target.transfer_source_hash == item["source_hash"]
            else "changed"
            if target
            else "name_conflict"
            if name_conflicts
            else "new"
        )
        if choice["action"] == "create" and state != "new":
            bad("新規作成できない設定が選択されています", 409)
        if choice["action"] == "append" and (not target or state == "identical"):
            bad("改訂を追加できない設定が選択されています", 409)
        if (
            choice["action"] == "append"
            and target.transfer_source_key is None
            and choice.get("target_id") != target.id
        ):
            bad("既存設定への紐付けは移行先IDを明示してください", 409)
        if choice["action"] == "skip" and choice.get("target_id"):
            bad("スキップ時に移行先設定は指定できません")
        diff = []
        if current:
            from src.services.menu_process_service import change_list

            diff = change_list(current, source_summary)
        plans.append(
            {
                "source_key": item["source_key"],
                "kind": item["kind"],
                "name": item["name"],
                "source_revision": item["source_revision"],
                "source_hash": item["source_hash"],
                "state": state,
                "action": choice["action"],
                "target_id": target.id if target else None,
                "target_version": target.version if target else 0,
                "target_hash": target.transfer_source_hash if target else None,
                "name_conflicts": [
                    {"id": row.id, "version": row.version} for row in name_conflicts
                ],
                "diff": diff,
            }
        )
    result = {
        "bundle_hash": bundle["bundle_hash"],
        "target_facility_id": facility.id,
        "items": plans,
    }
    result["preview_hash"] = digest(result)
    return result


@mutation_operation
def apply(session, bundle, target_facility_id, choices, preview_hash):
    plan = preview(session, bundle, target_facility_id, choices)
    if plan["preview_hash"] != preview_hash:
        bad("移行先の設定が確認後に変わりました。差分を再確認してください", 409)
    if not any(item["action"] != "skip" for item in plan["items"]):
        bad("適用する設定を選択してください")
    # Transfers do not lock menus/bindings. Acquire every existing recipe in
    # the same order before any profile/revision/binding-related write.
    lock_recipes(session, [row["target_id"] for row in plan["items"]
                          if row["kind"] == "recipe" and row["action"] != "skip"
                          and row["target_id"] is not None])
    profile_ids = sorted({row["target_id"] for row in plan["items"]
                          if row["kind"] != "recipe" and row["action"] != "skip"
                          and row["target_id"] is not None})
    for profile_id in profile_ids:
        if session.execute(update(ProcessRouteProfile).where(ProcessRouteProfile.id == profile_id)
                .values(version=ProcessRouteProfile.version),
                execution_options={"synchronize_session": False}).rowcount != 1:
            bad("移行先の設定が更新されました", 409)
    by_key = {item["source_key"]: item for item in bundle["items"]}
    for row in plan["items"]:
        if row["action"] == "skip":
            continue
        item = by_key[row["source_key"]]
        model = ProcessRecipe if item["kind"] == "recipe" else ProcessRouteProfile
        target = session.get(model, row["target_id"]) if row["target_id"] else None
        if target:
            number = target.version + 1
            if (
                session.execute(
                    update(model)
                    .where(
                        model.id == target.id, model.version == row["target_version"]
                    )
                    .values(
                        version=number,
                        name=item["name"],
                        transfer_source_key=item["source_key"],
                        transfer_source_hash=item["source_hash"],
                    ),
                    execution_options={"synchronize_session": False},
                ).rowcount
                != 1
            ):
                bad("移行先の設定が更新されました", 409)
            session.refresh(target)
        else:
            number = 1
            target = model(
                facility_id=target_facility_id,
                name=item["name"],
                version=number,
                transfer_source_key=item["source_key"],
                transfer_source_hash=item["source_hash"],
            )
            session.add(target)
            session.flush()
        if item["kind"] == "recipe":
            revision = ProcessRevision(
                recipe_id=target.id,
                number=number,
                parent_revision_id=row["target_id"] and target.head_revision_id,
                menu_execution_id=None,
                scope="menu",
                status="confirmed",
                origin="transfer",
                actor_role="admin",
                document=deepcopy(item["document"]),
                source_snapshot={
                    "transfer_source_key": item["source_key"],
                    "source_hash": item["source_hash"],
                },
                changes=row["diff"],
                user_constraints={},
                change_note=f"stg工程マスター版{item['source_revision']}を選択移行",
            )
        else:
            revision = ProcessRouteProfileRevision(
                profile_id=target.id,
                number=number,
                parent_revision_id=row["target_id"] and target.head_revision_id,
                status=item["status"],
                template_id=item["template_id"],
                template_version=item["template_version"],
                svg_snapshot=item["svg_snapshot"],
                manifest_snapshot=deepcopy(item["manifest_snapshot"]),
                network_snapshot=deepcopy(item["network_snapshot"]),
                snapshot_hashes=deepcopy(item["snapshot_hashes"]),
                document=deepcopy(item["document"]),
                metadata_json={
                    "transfer_source_key": item["source_key"],
                    "source_hash": item["source_hash"],
                },
                change_note=f"stg施設図面版{item['source_revision']}を選択移行",
            )
        session.add(revision)
        session.flush()
        target.head_revision_id = revision.id
        if item["kind"] == "recipe" or item["status"] == "confirmed":
            target.confirmed_revision_id = revision.id
        session.flush()
    return {
        "applied": [
            item["source_key"] for item in plan["items"] if item["action"] != "skip"
        ],
        "target_facility_id": target_facility_id,
        "bundle_hash": bundle["bundle_hash"],
    }
