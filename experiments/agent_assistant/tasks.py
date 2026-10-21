"""Named operator tasks. Instructions are owned by the application, not typed prompts."""
from .presentation import TASK_DESCRIPTIONS
TASKS = {
    "hospital_pending_faxes": {
        "id": "hospital_pending_faxes", "domain": "hospital", "label": "未処理FAXを処理する",
        "description": "未処理FAXを取得し、OCRが終わったものから確認します。内容を確認して承認すると、確定・帳票作成まで進みます。",
        "steps": ["未処理FAXを取得", "OCR処理", "エージェントが原本・結果を照合", "利用者が確認・承認", "確定・帳票作成"],
        "instruction": """選択された定型業務は「未処理FAXを処理する」です。
最初にfax_work_queueを呼ぶ。この依頼開始時に取得した対象だけを処理する。
未処理の原本は既に登録済みである。原本・施設・週・正本版の検証を省略せず、pendingのFAXはhospital_start_faxで開始する。processingは再実行せず待つ。
OCRが完了した対象から、原本PDFとOCR結果・数量シートを照合し、必要な選択・原本に基づく訂正案を用意する。判断できない値は推測しない。
各対象の数量シート案が整ったらrequest_fax_approvalで対象FAX ID・確認結果・sheetを提出する。保存済みシートはget_sheet、未保存なら正規のget_sheet_sourceから取得し、利用者の既存編集を上書きしない。利用者承認前に認識承認・シート確認記録・注文確定をしない。
一件の承認待ちで他のFAXの処理を止めない。fax_work_queueを取得し、次のOCR完了対象を確認する。利用者の不承認・訂正指示は対象ごとに反映し再確認を依頼する。
blockedのFAXは履歴の具体的な理由をask_operatorで利用者へ確認し、認識復旧や原本不足を推測で迂回しない。
すぐに進められる対象がない場合はwait_for_fax_workを呼び、OCR完了または利用者回答を待つ。何度もモデル側でポーリングしない。
承認済みの原本・採用OCR・数量が変わっていないことを確認してから、承認されたsheetと完全に一致する確認記録・保存・袋分け・確定を既存APIで行う。sheetの保存前には全ての既存検査を実行する。内容を変更したら再承認を得る。
確定後の状態とラベル・納品書・製造集計を対象order_idごとに取得し、対象FAXごとに記録する。全対象の確定・帳票取得後にfinish_workを呼ぶ。未処理がない場合はhospital_pending_faxesを再取得してその命令IDを完了証跡とし、0件と報告する。""",
    }
}


def task(ident, domain, label, kind, instruction, *, writes=(), terminal=(), exports=(), month=True, report=False):
    TASKS[ident] = dict(id=ident, domain=domain, label=label, description=instruction.split('。')[0]+'。',
        steps=['対象を選択', '処理・照合', '結果を確認・承認', '保存・出力・完了'], target_kind=kind,
        month=month, report=report, writes=list(writes), terminal=list(terminal), exports=list(exports),
        instruction=instruction+'''\n最初にget_task_contextで利用者が選択した対象・期間・追加指定を取得する。対象を推測・追加しない。
入力契約はdescribe_operationで読む。シフトはget_periodで正規の勤務期間を取得し、get_demands・get_workdaysのstart・endにそのstart_date・end_date_exclusiveを使う。暦月初日を推測しない。学校給食は全呼出しのqueryへ選択facility_idを指定する。原本・保存済み入力を再取得し、既存の計算・検査を使う。
非同期処理はwait_for_native_jobで完了を待つ。結果と入力のネイティブGET命令IDをrequest_task_approvalへ提出する。実行予定の確定操作があればactionsに操作名とparameters（観測済みrevisionを除くID/query/body）をそのまま提出する。
利用者の確認待ちはwait_for_task_decisionで待ち、反復モデル呼出しをしない。訂正依頼なら入力・結果を再取得して訂正し、新しい確認を出す。
承認後に提出済みの確定操作だけを実行する。保存後のGETと対象別の帳票を取得してfinish_workへ命令ID・成果物IDを提出する。新旧結果・対象・版の相違や不足は完了扱いにしない。''')


task('hospital_review_faxes', 'hospital', '要確認FAXを確認・訂正する', 'fax',
     '選択された要確認FAXの原本と失敗理由を取得する。認識訂正は原本セル画像と値を対比し、request_task_approvalにhospital_accept_recognitionの正確なsource_receipt・valuesを提出して人の承認後に採用する。再OCRが必要な場合も原本・施設・週の記録を検証し、再実行を人に承認してもらう。復旧後はfax_work_queueとrequest_fax_approvalを使い、数量表を再承認して保存・袋分け・確定・三帳票を取得する。',
     writes=(), terminal=('hospital_accept_recognition','hospital_retry_fax'), month=False)
task('hospital_outputs', 'hospital', '確定済み注文の帳票を作成する', 'order',
     '選択された確定済み注文だけを再取得して数量・施設・週を確認する。利用者が対象内容を承認した後、ラベル・納品書・製造集計を注文ごとに取得する。注文は変更しない。',
     exports=('hospital_export_labels','hospital_export_delivery_notes','hospital_export_aggregate'), month=False)
for ident, label, instruction, kind in [
    ('shift_next_month','翌月のシフト案を作る','選択月の必要人数・勤務条件・直前期間の確定記録を取得し、既存OR-Toolsで勤務案を作る。', 'month'),
    ('shift_wishes','希望休・勤務条件を反映して作り直す','選択月の登録済み希望休と勤務条件を取得してOR-Toolsで案を作り直す。利用者が指定していない希望休や条件を新設しない。', 'month'),
    ('shift_absence','急な欠勤時に組み直す','選択された職員と欠勤日にunavailable/hardだけを既存constraintsへ保存する。他の条件を変えず、必要人数と固定勤務を取得しOR-Toolsで選択月の代替案を作る。', 'staff')]:
    task(ident,'shift',label,kind,instruction+'候補を明示して勤務表・不足・違反と変更内容を提示し、利用者承認後にその候補だけを確定して出力する。',
         writes=('shift_preflight','shift_solve')+(('shift_save_constraint','shift_update_constraint') if kind=='staff' else ()),
         terminal=('shift_confirm',), exports=('shift_export',))
task('shift_check','shift','人員不足・勤務制約違反を確認する','candidate',
     '選択された勤務候補の勤務表とvalidationを取得し、人員不足・制約違反を一覧で提示する。問題があっても検査報告として明示し、利用者が確認したら報告を完了する。勤務・条件・確定状態を変更しない。',report=True)
task('shift_export','shift','確定した勤務表を出力する','confirmed_candidate',
     '選択された確定済みの勤務候補を取得し、利用者が対象と内容を確認・承認した後、その候補を明示して帳票を出力する。',exports=('shift_export',))
task('lunch_import','school-lunch','献立・調理手配書を取り込む','school',
     '利用者が学校と月を照合して添付した原本だけを抽出する。source_document_idをget_source_documentで再取得する。OCRの場合は完了した抽出ジョブとsource_reviewも取得し、原本と数量・単位・日付を対比して提示する。confirm_importの全入力を確認へ提出し、承認された内容だけを確定保存する。保存後の献立を取得し元の抽出内容と照合する。',
     writes=('lunch_import_source',),terminal=('lunch_save_source_review','lunch_confirm_import'))
for ident,label in [('lunch_process_routes','工程表・動線図を作る'),('lunch_regenerate','献立変更後に作り直す')]:
    task(ident,'school-lunch',label,'menu',
         '選択した学校・月・献立だけを使い、既存process/infer、get_schedule_draft、工程表保存、動線図APIで作成する。工程表の時間・担当は正規のget_schedule_draftの結果を使い、存在しない設定を手書きしない。献立の数量・アレルギー・担当を推測で変えない。必要な図面が複数なら利用者が選んだ図面IDを使う。対象日すべての工程・工程表・動線図と元の献立版を提示して承認を受ける。',
         writes=('lunch_infer_process','lunch_save_process','lunch_create_schedule','lunch_save_schedule','lunch_generate_route','lunch_save_route'),
         exports=('lunch_export_schedule','lunch_export_route'))
task('lunch_procurement','school-lunch','月間食材必要量・発注案を作る','school',
     '選択した学校・月の保存済み献立、在庫、価格、入荷、発注単位から既存monthly-procurementで必要量と発注案を計算する。不足項目は利用者に質問し、明示回答以外で埋めない。計算結果を提示して承認後に出力する。発注送信や発注確定は行わない。lunch_get_procurementが返すfingerprintをlunch_export_procurementのexpected_fingerprintにそのまま指定する。',
     writes=('lunch_save_procurement',),exports=('lunch_export_procurement',))
task('lunch_month_exports','school-lunch','月間帳票をまとめて出力する','school',
     '選択した学校・月の献立・工程表・動線図の全件を取得し、欠落日・古い入力・未確定内容がないか照合する。対象一覧を提示して承認後に月間工程表と月間動線図を出力する。expected_sourcesに取得した全ID・版・学校・日付を明示する。',
     exports=('lunch_export_month_schedules','lunch_export_month_routes'))

TASKS['lunch_import']['label']='【月次】献立・調理手配書を取り込む'
for legacy in ('lunch_process_routes','lunch_regenerate'):
    TASKS[legacy]['hidden']=True

task('lunch_daily_bundle','school-lunch','【日次】作業工程表・動線図・実施価格を一括作成する','menu',
     '学校と日付を選ぶと、その日の全献立から作業工程表・動線図・実施価格をまとめて作成します。月次取込をやり直さず、登録済み献立を取得する。'
     '不足情報・複数の図面・単価・実際の食数はask_operatorで質問する。図面はget_task_contextの候補から選び、複数ある場合は図面名（ID:n）の選択肢で質問して回答後にchoose_task_layoutを呼ぶ。'
     '全献立のget_processで保存済みの共有献立・工程パターンと当日の実績を取得する。標準パターンを当日の事実と取り違えず、当日回答はscope=dayだけへ保存する。未作成なら工程案の作成可否を利用者に質問し、既存infer_processで案を取得する。実施場所や釜を推測して保存しない。'
     'ask_lunch_process_questionを呼び、各献立の複数食材が関わる工程は、一緒に調理したかを先に確認する。一緒なら共通の場所・釜を、別々なら食材ごとの場所・釜を利用者へ確認する。不足する開始・終了時刻、担当者、汚染区分、作業段階も同じツールで確認する。回答後も同じツールを呼んで全工程の質問を終える。'
     'complete=trueになったらsave_lunch_process_answersでサーバーに保存させる。工程JSONを自分で転記・再構成しない。保存・再取得後にget_schedule_draftと動線図の作成へ進む。未回答を一般質問や既存設備の流用で迂回しない。'
     '時間・担当等の不足は続けて質問し、原本または利用者の回答だけで埋める。三帳票の結果承認は設備への回答とは別に必須。'
     '動線図の作成はprepare_lunch_daily_reviewが選択済みの学校・日付・図面から行う。図面ID・versionを自分で組み立てたり利用者へ質問したりしない。競合や採用済み原本の上書きを自動で回避しない。'
     '実施価格はget_price_quantitiesで各献立の正本1人量を取得し、get_price_draftsとget_price_sourceで対象日に登録済みの価格情報を確認する。別日・別校の価格を流用しない。'
     '学校・実施日・全source_menu_execution_idsを明示した実施価格入力をsave_price_sourceで保存する。必要な法人名・代表者名・原価・食数の未確定項目を推測せずチャットで確認する。'
     'その保存IDのget_price_resultで既存計算結果を取得する。三つを保存したらprepare_lunch_daily_reviewを呼び、サーバーが再取得した証跡を同じ確認画面へ提出させる。request_task_approvalの証跡IDやactionsを自分で組み立てない。'
     'wait_for_task_decisionで利用者の承認を待ち、承認後はcomplete_lunch_daily_workを呼ぶ。三帳票のID・出典版・fingerprintと完了証跡はサーバーに管理させ、三帳票が揃うまで完了にしない。',
     writes=('lunch_infer_process','lunch_save_process','lunch_create_schedule','lunch_save_schedule','lunch_generate_route','lunch_save_route','lunch_save_price_source'),
     exports=('lunch_export_schedule','lunch_export_route','lunch_export_price'),month=False)
TASKS['lunch_daily_bundle'].update(date=True,steps=['学校・日付を選択','不足情報をチャットで確認','作業工程表・動線図・実施価格を確認・承認','三帳票を保存・出力して完了'])
TASKS['lunch_daily_bundle']['instruction'] += '\n日次業務の承認・完了には専用手順を使う：prepare_lunch_daily_review → wait_for_task_decision → 承認後にcomplete_lunch_daily_work。汎用のrequest_task_approval・export各操作・finish_workの証跡IDやactionsを自分で組み立てない。工程表を保存するときはget_schedule_draftのsource_menu_versionsとsource_process_revisionsを欠落させず、そのまま渡す。原本工程表の時間目盛はtimeline_ticksに回答された8個を保存する。原価確認済みのcost_items.statusはconfirmed（未確認ならunconfirmed）を使い、日本語の表示名をAPIへ送らない。'
TASKS['lunch_daily_bundle']['instruction'] += '\n工程表の保存は専用のsave_lunch_daily_scheduleを使う。盛付開始・終了、原本の8個の時刻目盛、配送担当だけを利用者へ質問して渡す。lunch_create_schedule/lunch_save_scheduleの行JSON・版を自分で組み立てない。'
TASKS['lunch_daily_bundle']['instruction'] += '\n実施価格をlunch_save_price_sourceで保存すると、MCPが返されたdraft_idをこの日次業務の現在の価格に固定する。訂正を明示して再保存した場合も、その成功した保存結果を採用する。過去の生成分が一覧に残っていても、内部draft_idを利用者に選ばせない。保存後はprepare_lunch_daily_review→wait_for_task_decision→complete_lunch_daily_workを使う。'

# Public schema exposes operator choices, never policy lists or model instructions.
for ident in ('shift_next_month','shift_wishes','shift_absence'):
    TASKS[ident]['instruction'] = '''最初にget_task_contextで利用者が選択した月・対象・欠勤日を取得する。
不足する業務情報だけをask_operatorで質問する。登録済み条件・希望休は変えずprepare_shift_reviewを呼ぶ。
prepare_shift_reviewが正規条件・勤務期間・OR-Tools・入力版・検査・証跡・承認画面を管理する。生のpreflight/solveやrequest_task_approvalを自分で組み立てない。
wait_for_task_decisionで利用者承認を待つ。承認後はcomplete_shift_workを呼ぶ。サーバーが承認した候補だけ確定し、正規勤務表を出力・再照合して完了する。
訂正なら内容を確認し、登録済み条件の変更を推測しない。APIのID・版・証跡を利用者に質問しない。'''
TASKS['hospital_pending_faxes']['instruction'] = '''最初にfax_work_queueを呼び、開始時に固定された対象だけを処理する。
pendingはhospital_start_faxへ対象uploaded_pdf_idとbody.sha256（snapshot.sha256）とreasonを渡して実OCRを開始する。processingは再実行せずwait_for_fax_workで待つ。
readyの各FAXはprepare_fax_reviewにfax_idだけ渡す。返されたoriginal.idをread_artifact_pageで読み、sheetの全数量・施設・週と原本を照合する。未確定値は推測せずask_operatorで確認する。
原本との照合後request_fax_approvalへfax_idと日本語の照合結果summaryだけ渡す。変更しないsheetを転記しない。訂正が必要なら元シートの行以外は変えず、原本または明示回答に基づく修正だけを提出する。
一件の承認待ちでも他の対象の照合を続ける。すぐ進められる対象がなければwait_for_fax_workで待つ。blockedは具体的な業務上の不明点を利用者に質問し、認識復旧や不足情報を推測で迂回しない。
全対象を利用者が承認した後、complete_fax_workを呼ぶ。サーバーが承認済み正本シート・現在版を保持し、既存の保存前検査・確認記録・保存・袋分け・確定・正規ラベル・納品書・製造集計・完了判定を行う。各APIの内部ID・版・シートJSON・証跡を自分で転記しない。
0件でもcomplete_fax_workで現在の0件を再取得・照合する。モデルの文章だけでは完了にしない。'''

def public_tasks():
    hidden = {'instruction','writes','terminal','exports','report','hidden'}
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo("Asia/Tokyo"))
    current=today.strftime("%Y-%m")
    following=f"{today.year+(today.month==12):04}-{today.month%12+1:02}"
    return [{**{key:value for key,value in entry.items() if key not in hidden},"description":TASK_DESCRIPTIONS.get(entry["id"],entry["description"]),"default_month":following if entry["id"]=="shift_next_month" else current} for entry in TASKS.values() if not entry.get("hidden")]
