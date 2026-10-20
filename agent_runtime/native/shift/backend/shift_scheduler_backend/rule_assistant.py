from __future__ import annotations

import re

from .models import Staff


WEEKDAY_BY_LABEL = {
    "月": 0,
    "月曜": 0,
    "月曜日": 0,
    "火": 1,
    "火曜": 1,
    "火曜日": 1,
    "水": 2,
    "水曜": 2,
    "水曜日": 2,
    "木": 3,
    "木曜": 3,
    "木曜日": 3,
    "金": 4,
    "金曜": 4,
    "金曜日": 4,
}
ALL_DAY_LABELS = {
    **WEEKDAY_BY_LABEL,
    "土": 5,
    "土曜": 5,
    "土曜日": 5,
    "日": 6,
    "日曜": 6,
    "日曜日": 6,
}
WEEKDAY_NAMES = {
    0: "月曜日",
    1: "火曜日",
    2: "水曜日",
    3: "木曜日",
    4: "金曜日",
    5: "土曜日",
    6: "日曜日",
}
_UNSET = object()


def _default_staff_rule(staff_id: int) -> dict:
    return {
        "staff_id": staff_id,
        "fixed_weekday_off": None,
        "allowed_weekday_offs": None,
        "fixed_weekday_off_priority": "hard",
        "consecutive_weekend_off_count": 0,
        "consecutive_weekend_off_priority": "hard",
        "preference_weight": 100,
    }


def _extract_weekdays(text: str, *, include_weekend: bool = False) -> list[int]:
    label_map = ALL_DAY_LABELS if include_weekend else WEEKDAY_BY_LABEL
    values = []
    for token in re.findall(r"[月火水木金土日](?:曜日|曜)?", text):
        weekday = label_map.get(token)
        if weekday is not None and weekday not in values:
            values.append(weekday)
    return values


def _subject_clauses(
    text: str, staffs: list[Staff], questions: list[str]
) -> list[tuple[list[Staff], str]]:
    by_name: dict[str, list[Staff]] = {}
    for staff in staffs:
        if staff.name:
            by_name.setdefault(staff.name, []).append(staff)
    if not by_name:
        return [([], text)]
    # Alternation order makes each consumed name the longest match at its position.
    names = re.compile("|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True)))
    clauses: list[tuple[list[Staff], str]] = []
    sentences = []
    for sentence in re.split(r"[。！？!?;；\n\r]+", text):
        clause = ""
        for part in re.split(r"([、,])", sentence):
            if part in {"、", ","} and re.search(r"(?:は|が|固定休|定休|休み|有給)", names.sub("", clause)):
                sentences.append(clause)
                clause = ""
            else:
                clause += part
        sentences.append(clause)
    for sentence in sentences:
        if not sentence:
            continue
        matches = list(names.finditer(sentence))
        if not matches:
            clauses.append(([], sentence))
            if re.search(r"(?:平日休み|固定休|定休|有給|[月火水木金]曜日休み)", sentence):
                questions.append(f"対象者を登録名で指定してください: {sentence}")
            continue
        groups: list[list[re.Match]] = []
        for match in matches:
            if groups and re.fullmatch(
                r"(?:さん)?(?:と|、|,|・|および|及び|ならびに)",
                sentence[groups[-1][-1].end():match.start()],
            ):
                groups[-1].append(match)
            else:
                groups.append([match])
        sentence_clauses: list[tuple[list[Staff], str]] = []
        for index, group in enumerate(groups):
            end = groups[index + 1][0].start() if index + 1 < len(groups) else len(sentence)
            prefix = sentence[:group[0].start()] if index == 0 else ""
            predicate = sentence[group[-1].end():end].rstrip("、,")
            predicate_body = re.sub(r"^(?:さん)?(?:は|の|が|も)?", "", predicate, count=1)
            # Only grammatical particles belonging to supported conditions are scoped.
            # Another topic/subject particle means an unresolved subject, even without punctuation.
            unscoped = re.sub(
                r"少なくとも|(?:平日(?:の)?休み|土日(?:の)?連休|有給|[月火水木金土日](?:曜日|曜)?|\d+人)(?:は|が|を|も)?",
                "", predicate_body,
            )
            ambiguous = (
                bool(prefix)
                or re.search(r"[はがも]", unscoped) is not None
                or any(len(by_name[match.group()]) != 1 for match in group)
                or not re.match(
                    r"^(?:さん)?(?:は|の|が|も|[月火水木金土日]|平日|まだ|有給|\d+人)",
                    predicate,
                )
            )
            if ambiguous:
                questions.append(f"対象者と条件を確認してください: {sentence[group[0].start():end]}")
                break
            subjects = list({
                by_name[match.group()][0].id: by_name[match.group()][0] for match in group
            }.values())
            sentence_clauses.append((subjects, predicate))
        else:
            clauses.extend(sentence_clauses)
    return clauses


def build_rule_candidates(*, text: str, staffs: list[Staff], settings: dict) -> dict:
    normalized = re.sub(r"[^\S\n\r]+", "", text)
    existing_by_staff = {
        int(row["staff_id"]): dict(row) for row in settings.get("staff_weekend_rules") or []
    }
    explanations: list[str] = []
    questions: list[str] = []
    pair_rule_candidates: list[dict] = []
    staff_updates: list[dict] = []
    clauses = _subject_clauses(normalized, staffs, questions)
    mentioned = list({staff.id: staff for subjects, _ in clauses for staff in subjects}.values())

    for staff, nearby in ((staff, predicate) for subjects, predicate in clauses for staff in subjects):
        row = existing_by_staff.get(staff.id, _default_staff_rule(staff.id))
        changed = False

        fixed_match = re.search(
            r"([月火水木金](?:曜日|曜)?)(?:固定休|定休|休み|を休み|は休み)",
            nearby,
        )
        if fixed_match:
            weekday = WEEKDAY_BY_LABEL[fixed_match.group(1)]
            row["fixed_weekday_off"] = weekday
            row["fixed_weekday_off_priority"] = (
                "prefer" if any(token in nearby for token in ["基本", "なるべく", "できれば"]) else "hard"
            )
            changed = True
            explanations.append(f"{staff.name}: {fixed_match.group(1)}を固定休みにする")

        if re.search(r"(?:平日休み|平日の休み)(?:は|を)?(?:なし|不要|作らない)", nearby):
            row["allowed_weekday_offs"] = []
            changed = True
            explanations.append(f"{staff.name}: 追加の平日休みは入れない")
        else:
            allowed_match = re.search(r"(?:平日休み|平日の休み)(?:は|を)?(.+?)だけ", nearby)
            if allowed_match:
                weekdays = _extract_weekdays(allowed_match.group(1))
                if weekdays:
                    row["allowed_weekday_offs"] = weekdays
                    changed = True
                    weekday_labels = "・".join(WEEKDAY_NAMES[value] for value in weekdays)
                    explanations.append(f"{staff.name}: 追加の平日休みは{weekday_labels}だけ")
            elif re.search(r"(?:平日休み|平日の休み)(?:は|を)?(?:制限なし|自由)", nearby):
                row["allowed_weekday_offs"] = None
                changed = True
                explanations.append(f"{staff.name}: 追加の平日休みは曜日制限なし")

        if changed:
            existing_by_staff[staff.id] = row

        if re.search(r"(?:まだ)?有給(?:が|は)?ありません", nearby):
            update = {"staff_id": staff.id, "paid_leave_eligible": False}
            if update not in staff_updates:
                staff_updates.append(update)
                explanations.append(f"{staff.name}: 有休0.5を使用しない")

    for subjects, predicate in clauses:
        consecutive_match = re.search(r"土日(?:の)?連休(?:が|を)?(\d+)回", predicate)
        if not consecutive_match:
            continue
        count = int(consecutive_match.group(1))
        targets = subjects or (mentioned if predicate.startswith("各人") else [])
        if targets:
            for staff in targets:
                row = existing_by_staff.get(staff.id, _default_staff_rule(staff.id))
                row["consecutive_weekend_off_count"] = count
                row["consecutive_weekend_off_priority"] = "hard"
                existing_by_staff[staff.id] = row
            explanations.append(f"記載された{len(targets)}名: 土日連休を{count}回作る")
        else:
            questions.append("土日連休を設定する対象者を選択してください。")

    coverage_ids: list[int] | None = None
    coverage_minimum: int | None = None
    coverage_maximum: int | None = None
    coverage_target: int | None = None
    coverage_clauses = [
        (subjects, predicate) for subjects, predicate in clauses
        if re.search(r"\d+人(?:の)?(?:内|うち)", predicate)
    ]
    coverage_subjects, coverage_text = coverage_clauses[0] if len(coverage_clauses) == 1 else ([], "")
    if len(coverage_clauses) > 1:
        questions.append("人数条件が複数あります。適用する対象者と条件を確認してください。")
    coverage_people = coverage_subjects or mentioned
    coverage_scope = "土日・祝日" if "祝日" in coverage_text or "土日祝" in coverage_text else "土日"
    coverage_group_match = re.search(r"(\d+)人(?:の)?(?:内|うち)", coverage_text)
    coverage_minimum_match = re.search(r"(?:必ず|最低|少なくとも)(\d+)人(?:は|以上)?出勤", coverage_text)
    if coverage_minimum_match is None:
        coverage_minimum_match = re.search(r"(\d+)人以上出勤", coverage_text)
    coverage_maximum_match = re.search(r"(\d+)人以下出勤", coverage_text)
    coverage_target_match = re.search(r"(?:できれば|目標|target)(\d+)人(?:出勤)?", coverage_text)
    if (
        coverage_group_match
        and (coverage_minimum_match or coverage_maximum_match or coverage_target_match)
    ):
        expected_group_size = int(coverage_group_match.group(1))
        coverage_minimum = (
            int(coverage_minimum_match.group(1)) if coverage_minimum_match else None
        )
        coverage_maximum = (
            int(coverage_maximum_match.group(1)) if coverage_maximum_match else None
        )
        coverage_target = (
            int(coverage_target_match.group(1)) if coverage_target_match else None
        )
        if len(coverage_people) == expected_group_size:
            coverage_ids = [staff.id for staff in coverage_people]
            parts = []
            if coverage_minimum is not None:
                parts.append(f"{coverage_minimum}人以上")
            if coverage_maximum is not None:
                parts.append(f"{coverage_maximum}人以下")
            if coverage_target is not None:
                parts.append(f"目標 {coverage_target}人")
            explanations.append(
                f"{', '.join(staff.name for staff in coverage_people)}: {coverage_scope}は{' / '.join(parts)}出勤"
            )
        else:
            questions.append(
                f"{coverage_scope}の人数条件を適用する{expected_group_size}名を選択してください。"
            )

    if "祝日" in normalized:
        explanations.append("日本の祝日を自動判定する")
        if any(token in normalized for token in ["土日扱い", "土日祝", "特日", "祝日も同じ"]):
            explanations.append("祝日を土日と同じ特日として扱う")

    auto_half_day_allowed_weekdays: list[int] | None | object = _UNSET
    if re.search(r"(?:半日勤務|斜線勤務).*(?:制限なし|毎日|いつでも)", normalized):
        auto_half_day_allowed_weekdays = None
        explanations.append("自動の半日勤務は曜日制限なし")
    else:
        auto_half_day_match = re.search(r"(?:半日勤務|斜線勤務)は(.+?)だけ", normalized)
        if auto_half_day_match:
            weekdays = _extract_weekdays(auto_half_day_match.group(1), include_weekend=True)
            if weekdays:
                auto_half_day_allowed_weekdays = weekdays
                weekday_labels = "・".join(WEEKDAY_NAMES[value] for value in weekdays)
                explanations.append(f"自動の半日勤務は{weekday_labels}だけ許可する")
        elif (
            ("平日" in normalized or "月曜日" in normalized)
            and "土曜" in normalized
            and re.search(r"(?:半日勤務|半日|斜線勤務).*(?:なし|ありません|禁止)", normalized)
        ):
            auto_half_day_allowed_weekdays = [6]
            explanations.append("自動の半日勤務は日曜日だけ許可する")

    for subjects, predicate in clauses:
        if not re.search(r"一緒に休(?:めません|まない|ませない)", predicate):
            continue
        if len(subjects) != 2:
            questions.append("一緒に休ませない対象の2名を指定してください。")
            continue
        first, second = subjects
        if not any(
            {row["first_staff_id"], row["second_staff_id"]} == {first.id, second.id}
            for row in pair_rule_candidates
        ):
            pair_rule_candidates.append(
                {
                    "first_staff_id": min(first.id, second.id),
                    "second_staff_id": max(first.id, second.id),
                    "kind": "avoid_same_day_off",
                    "strength": "hard",
                    "weight": 100,
                    "reason": "文章から作成した全日の同時休み禁止",
                }
            )
            explanations.append(f"{first.name}・{second.name}: 全日で一緒に休ませない")

    holiday_match = re.search(r"(?:合計|月)(\d+(?:\.5)?)日", normalized)
    if holiday_match is None:
        holiday_match = re.search(r"休み(?:は|を)?(?:合計)?(\d+(?:\.5)?)日", normalized)
    exact_holidays = float(holiday_match.group(1)) if holiday_match else None
    if exact_holidays is not None:
        explanations.append(f"休日換算を全員{exact_holidays:g}日にする")

    settings_patch: dict = {"staff_weekend_rules": list(existing_by_staff.values())}
    if coverage_ids is not None:
        settings_patch["weekend_coverage_staff_ids"] = coverage_ids
        settings_patch["weekend_coverage_priority"] = "hard"
        settings_patch["weekend_adjustment_policy"] = "ask"
        if coverage_minimum is not None:
            settings_patch["weekend_coverage_min_working"] = coverage_minimum
        if coverage_maximum is not None:
            settings_patch["weekend_coverage_max_working"] = coverage_maximum
        if coverage_target is not None:
            settings_patch["weekend_coverage_target_working"] = coverage_target
    if exact_holidays is not None:
        settings_patch.update(
            {
                "monthly_holiday_exact_days": exact_holidays,
                "monthly_holiday_min_days": None,
                "monthly_holiday_max_days": None,
            }
        )
    if "祝日" in normalized:
        settings_patch["auto_apply_japanese_holidays"] = True
    if auto_half_day_allowed_weekdays is not _UNSET:
        settings_patch["auto_half_day_allowed_weekdays"] = auto_half_day_allowed_weekdays

    if not explanations:
        questions.append("人名、曜日、休日日数、土日の人数条件を文章に含めてください。")
    return {
        "provider": "structured_rule_parser",
        "settings_patch": settings_patch,
        "explanations": explanations,
        "questions": questions,
        "requires_confirmation": True,
        "pair_rule_candidates": pair_rule_candidates,
        "staff_updates": staff_updates,
    }
