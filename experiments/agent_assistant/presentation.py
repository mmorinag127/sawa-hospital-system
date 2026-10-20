"""Operator-facing descriptions and output names, separate from tool policy."""
EXPORT_LABELS = {
    'hospital_export_labels':'ラベル','hospital_export_delivery_notes':'納品書','hospital_export_aggregate':'製造集計',
    'shift_export':'勤務表','lunch_export_schedule':'作業工程表','lunch_export_route':'動線図','lunch_export_price':'実施価格表',
    'lunch_export_month_schedules':'月間作業工程表','lunch_export_month_routes':'月間動線図','lunch_export_procurement':'月間食材必要量・在庫・発注案',
}
TASK_DESCRIPTIONS = {
    'hospital_review_faxes':'要確認のFAXを選び、原本と認識結果を確認・訂正します。内容を承認すると、注文確定と帳票作成まで進みます。',
    'hospital_outputs':'確定済みの注文を選び、内容を確認・承認すると、ラベル・納品書・製造集計をまとめて作成します。',
    'shift_next_month':'対象月を選ぶと、登録済みの必要人数・希望休・勤務条件からシフト案を作成します。確認・承認後、勤務表を確定して出力します。',
    'shift_wishes':'登録済みの希望休・勤務条件を反映してシフト案を作り直します。確認・承認後、勤務表を確定して出力します。',
    'shift_absence':'欠勤する職員と日付を選ぶと、欠勤を反映した代替シフト案を作成します。確認・承認後、勤務表を確定して出力します。',
    'shift_check':'勤務案の人員不足・勤務条件の違反を確認して報告します。勤務表や条件は変更しません。',
    'shift_export':'確定済みの勤務表を選び、内容を確認・承認すると帳票を出力します。',
    'lunch_import':'学校・対象月を選び、献立・調理手配書を添付します。読取結果を確認・承認すると、その月の献立を登録します。',
    'lunch_procurement':'学校・対象月を選ぶと、献立・在庫・単価から必要食材と発注案を計算します。不足情報はチャットで確認し、承認後に帳票を出力します。',
    'lunch_month_exports':'学校・対象月の保存済み工程表・動線図を確認し、承認後に月間帳票をまとめて出力します。未作成・変更後の未確認データがあればお知らせします。',
}
