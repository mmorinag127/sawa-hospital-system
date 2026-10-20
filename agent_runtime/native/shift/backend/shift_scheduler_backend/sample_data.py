from datetime import date, time, timedelta

from sqlalchemy.orm import Session
from .editor_revision_service import dev_seed_write_snapshot

from .models import Availability, Demand, Position, ShiftTemplate, Skill, Staff, StaffSkill


def seed_sample_data(session: Session) -> None:
    with dev_seed_write_snapshot(session):
        _populate_sample_data(session)


def _populate_sample_data(session: Session) -> None:
    if session.query(Staff).first():
        return

    kitchen = Skill(name="kitchen")
    hall = Skill(name="hall")
    session.add_all([kitchen, hall])
    session.flush()

    staff = [
        Staff(name="Aki"),
        Staff(name="Ben"),
        Staff(name="Chika"),
        Staff(name="Dai"),
    ]
    session.add_all(staff)
    session.flush()

    session.add_all(
        [
            StaffSkill(staff_id=staff[0].id, skill_id=kitchen.id),
            StaffSkill(staff_id=staff[1].id, skill_id=kitchen.id),
            StaffSkill(staff_id=staff[2].id, skill_id=hall.id),
            StaffSkill(staff_id=staff[3].id, skill_id=hall.id),
            StaffSkill(staff_id=staff[0].id, skill_id=hall.id),
            StaffSkill(staff_id=staff[3].id, skill_id=kitchen.id),
        ]
    )

    kitchen_position = Position(name="Kitchen", required_skill_id=kitchen.id)
    hall_position = Position(name="Hall", required_skill_id=hall.id)
    day_shift = ShiftTemplate(
        name="Day", start_time=time(9, 0), end_time=time(17, 0), break_minutes=60
    )
    half_day_shift = ShiftTemplate(
        name="Half day",
        display_name="半日 4～8時",
        shift_type="half_day",
        start_time=time(4, 0),
        end_time=time(8, 0),
        break_minutes=0,
    )
    session.add_all([kitchen_position, hall_position, day_shift, half_day_shift])
    session.flush()

    start = date(2026, 8, 1)
    for offset in range(7):
        work_date = start + timedelta(days=offset)
        session.add_all(
            [
                Demand(
                    work_date=work_date,
                    shift_template_id=day_shift.id,
                    position_id=kitchen_position.id,
                    required_count=1,
                ),
                Demand(
                    work_date=work_date,
                    shift_template_id=day_shift.id,
                    position_id=hall_position.id,
                    required_count=1,
                ),
            ]
        )

    session.add_all(
        [
            Availability(
                staff_id=staff[0].id,
                work_date=date(2026, 8, 2),
                status="unavailable",
                strength="hard",
                reason="sample hard unavailable",
            ),
            Availability(
                staff_id=staff[2].id,
                work_date=date(2026, 8, 3),
                status="request_off",
                strength="soft",
                weight=25,
                reason="sample soft request off",
            ),
        ]
    )
