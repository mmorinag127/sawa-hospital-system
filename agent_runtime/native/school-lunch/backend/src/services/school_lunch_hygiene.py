"""Email rules applied to explicit phases, never inferred from free text."""
from collections import defaultdict

PHASE_ZONES = {"prep": {"contaminated", "contaminated_meat_fish"}, "wash_open": {"contaminated", "contaminated_meat_fish"}, "cook": {"non_contaminated"}, "heat": {"non_contaminated"}, "plate": {"non_contaminated"}}


def task_assignees(row, task):
    """Null is explicit legacy/shared ownership; [] is unknown, not shared."""
    return row.get("assignees", []) if task.get("assignees") is None else task["assignees"]


def ownership_rows(rows):
    """Render contiguous task groups with their actual owner set."""
    result = []
    for row in rows:
        group = None
        for task in row["tasks"]:
            names = task_assignees(row, task)
            if group is None or group["assignees"] != names:
                group = {**row, "assignees": names, "tasks": []}
                result.append(group)
            group["tasks"].append(task)
    return result


def missing_task_times(schedule):
    return [f"{schedule.get('service_date', '')} {row['menu_name']}・{task['operation']}: {label}"
            for row in schedule["rows"] for task in row["tasks"]
            for key, label in (("start_time", "開始時刻"), ("end_time", "終了時刻"))
            if task.get(key) is None]


def require_task_times(schedule):
    missing = missing_task_times(schedule)
    if missing:
        raise ValueError("時刻未入力の下書きです。正式な計算・出力前に入力してください: " + "、".join(missing))
    for row in schedule["rows"]:
        for task in row["tasks"]:
            if task["end_time"] <= task["start_time"]:
                raise ValueError(f"作業終了は開始より後にしてください: {row['menu_name']}・{task['operation']}")


def inspect_hygiene(schedule, *, require_complete=False):
    missing, events = missing_task_times(schedule), []
    if require_complete:
        require_task_times(schedule)
    kettles, people = defaultdict(list), defaultdict(list)
    tasks = []
    for ri, row in enumerate(schedule["rows"]):
        for ti, task in enumerate(row["tasks"]):
            key = (ri, ti)
            tasks.append(task)
            phase = task.get("phase")
            if not phase:
                missing.append(f"{row['menu_name']}・{task['operation']}: 作業段階")
            if task.get("work_zone") is None:
                missing.append(f"{row['menu_name']}・{task['operation']}: 汚染区分")
            elif phase in PHASE_ZONES and task["work_zone"] not in PHASE_ZONES[phase]:
                raise ValueError(f"作業段階と汚染区分が一致しません: {row['menu_name']}・{task['operation']}")
            assignees = task_assignees(row, task)
            if not assignees:
                missing.append(f"{row['menu_name']}・{task['operation']}: 担当者")
            for name in assignees:
                people[name].append((task, key))
            if phase == "heat" and task.get("end_time") is not None:
                events.append({"row": ri, "task": ti, "time": task["end_time"], "label": "中心温度測定", "assignees": assignees})
            if task.get("kettle"):
                if task.get("kettle_use") is None or task.get("uses_raw_meat") is None:
                    missing.append(f"{row['menu_name']}・釜{task['kettle']}: 使用回・生肉使用")
                else:
                    kettles[task["kettle"]].append(task)
    for kettle, uses in kettles.items():
        ordered = sorted(uses, key=lambda task: task["kettle_use"])
        if len({t["kettle_use"] for t in uses}) != len(uses):
            raise ValueError(f"釜{kettle}の使用回が重複しています。")
        meat_seen = False
        for index, task in enumerate(ordered):
            if task.get("start_time") is not None and any(
                previous.get("end_time") is not None and task["start_time"] < previous["end_time"]
                for previous in ordered[:index]
            ):
                raise ValueError(f"釜{kettle}の使用順と時刻が矛盾、または重複しています。")
            if meat_seen and not task["uses_raw_meat"]:
                raise ValueError(f"釜{kettle}: 生肉を使う調理は最後にしてください。")
            meat_seen = meat_seen or task["uses_raw_meat"]
    for name, work in people.items():
        known_work = [item for item in work if item[0].get("start_time") is not None and item[0].get("end_time") is not None]
        ordered_work = sorted(known_work, key=lambda item: item[0]["start_time"])
        for (previous_task, _), (current_task, _) in zip(ordered_work, ordered_work[1:]):
            if current_task["start_time"] < previous_task["end_time"]:
                raise ValueError(f"assignee has overlapping work tasks: {name}")
        previous = None
        # An unknown interval prevents ordering this person's transitions.
        if len(known_work) != len(work):
            continue
        for task, (ri, ti) in ordered_work:
            phase = task.get("phase")
            label = None
            if (previous == "prep" and phase == "wash_open") or (phase == "plate" and previous != "plate"):
                label = "エプロン・手袋・手洗い"
            elif previous == "wash_open" and phase in {"cook", "heat"}:
                label = "手袋・手洗い"
            if label:
                events.append({"row": ri, "task": ti, "time": task["start_time"], "label": label, "assignees": [name]})
            previous = phase
    if tasks and not missing_task_times(schedule):
        events.extend([
            {"row": None, "task": None, "time": min(t["start_time"] for t in tasks), "label": "残留塩素測定（作業開始）", "assignees": []},
            {"row": None, "task": None, "time": max(t["end_time"] for t in tasks), "label": "残留塩素測定（作業終了）", "assignees": []},
        ])
    if require_complete and missing:
        raise ValueError("衛生・釜ルールの確認に必要な入力が未設定です: " + "、".join(missing))
    return {"missing": missing, "events": sorted(events, key=lambda event: event["time"])}
