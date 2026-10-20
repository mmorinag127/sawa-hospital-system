from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.models.school_lunch import Facility
from src.models.school_lunch_timing import TimingProfile
from src.services.school_lunch_common_service import SchoolLunchConflict, SchoolLunchEntityNotFound


def serialize(session, profile):
    return {"id": profile.id, "facility_name": session.get(Facility, profile.facility_id).name,
            "name": profile.name, "plating_start": profile.plating_start, "plating_end": profile.plating_end,
            "timeline_ticks": profile.timeline_ticks, "version": profile.version}


def list_profiles(session, facility_id=None):
    query = select(TimingProfile).order_by(TimingProfile.id)
    if facility_id is not None:
        query = query.where(TimingProfile.facility_id == facility_id)
    return [serialize(session, item) for item in session.scalars(query)]


def save_profile(session, payload, profile_id=None):
    facility = session.scalar(select(Facility).where(Facility.name == payload["facility_name"]))
    if facility is None:
        raise SchoolLunchEntityNotFound("学校が登録されていません。学校選択画面で登録してください。")
    values = {key: payload[key] for key in ("name", "plating_start", "plating_end", "timeline_ticks")}
    values["facility_id"] = facility.id
    try:
        if profile_id is None:
            profile = TimingProfile(**values, version=1)
            session.add(profile)
            session.flush()
        else:
            if session.get(TimingProfile, profile_id) is None:
                raise SchoolLunchEntityNotFound("時間設定が見つかりません。")
            result = session.execute(update(TimingProfile).where(
                TimingProfile.id == profile_id, TimingProfile.version == payload["version"]
            ).values(**values, version=payload["version"] + 1).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise SchoolLunchConflict("時間設定が更新されています。再読み込みしてください。")
            session.flush()
            session.expire_all()
            profile = session.get(TimingProfile, profile_id)
    except IntegrityError as exc:
        session.rollback()
        raise SchoolLunchConflict("同じ学校・名前の時間設定が登録されています。") from exc
    return serialize(session, profile)


def calculate(session, payload):
    profile = session.get(TimingProfile, payload["profile_id"])
    if profile is None:
        raise SchoolLunchEntityNotFound("時間設定が見つかりません。")
    data = serialize(session, profile)
    if data["facility_name"] != payload["facility_name"]:
        raise ValueError("選択した時間設定は別の学校のものです。")
    if profile.version != payload["profile_version"]:
        raise SchoolLunchConflict("時間設定が更新されています。再読み込みしてください。")
    anchor = profile.plating_start.hour * 60 + profile.plating_start.minute
    tasks = []
    for task in payload["tasks"]:
        if any(type(task.get(key)) is not int for key in ("duration_minutes", "offset_before_plating_minutes")) or task["duration_minutes"] <= 0:
            raise ValueError(f"逆算する作業の所要分数と盛付前の差を入力してください: {task.get('key', '')}")
        end = anchor - task["offset_before_plating_minutes"]
        start = end - task["duration_minutes"]
        if not 0 <= start < end < 1440:
            raise ValueError("日付をまたぐ工程は現在の同日工程表に保存できません。所要時間と盛付からの差を確認してください。")
        tasks.append({"key": task["key"], "start_time": f"{start // 60:02d}:{start % 60:02d}",
                      "end_time": f"{end // 60:02d}:{end % 60:02d}"})
    return {"profile": data, "tasks": tasks}
