# 発注書 月次入力・週次シート生成 設計書

更新日: 2026-03-24

## 背景

- 発注書作成担当ユーザーは、月次メニューがアップロードされ次第、施設へ送付するための発注書を早期に準備したい。
- 実運用の発注書は、1施設あたり「1か月1ファイル」で、月内を週ごとのシートに分けたExcelになっている。
- 現在の注文書生成画面は入力としてはすでに月単位だが、生成物はFAX運用向けの実帳票ではなく、単一シートの汎用Excelである。
- 今回は、現行の月入力UIを活かしつつ、実運用に沿った「月次入力 -> 月1ファイル -> 週次シート群」の生成へ置き換える。

## 目的

- `facility_id + month_id` を入力として、施設ごとに1か月分の発注書Excelを1ファイルで生成する。
- 生成物の各シートは、月内の週次レンジごとに分かれたFAX送付用の帳票とする。
- ベースラインテンプレート、施設テンプレート、完成した発注書の責務を分離したまま、最終生成物を自動で作れるようにする。
- テスト可能な週次レンジ仕様、出力仕様、回帰確認手順を明文化する。

## 対象範囲

- 月次メニューから週次シート群を持つ発注書Excelを生成する仕様
- 週次レンジの定義
- UI/API/バックエンド生成フローの変更方針
- 施設設定と帳票テンプレートの適用方針
- テスト戦略、受け入れ条件、回帰確認項目

## 対象外

- セル内を `初回 / 修正1 / 修正2` に3分割する設計
- 3分割セルに対応したOCRデータモデル変更
- 発注書の自動FAX送信スケジューリング
- 生成後のPDF化やFAX連携の詳細実装

3分割セルは別設計で扱う。現段階では、既存の `変更1` / `変更2` は独立列のままとする。

## 現状整理

### 画面とAPI

- 画面はすでに `施設` と `対象月` を入力する作りになっている。
- `対象月` は `type="month"` で入力され、APIには `month_id` として送られる。
- APIは `POST /order-forms/generate` でExcelを返す。

参照:
- `frontend/src/pages/order-forms.tsx`
- `backend/src/api/order_forms.py`

### 現行の生成処理

- `build_order_form_excel()` は新規Workbookを作成し、`注文書` シート1枚に月次メニューを平坦に並べている。
- 生成物は汎用表であり、実運用のFAX帳票とは構造が異なる。
- 一方、FAX帳票の試作系は、実ファイル由来のテンプレート複製で構成されている。

参照:
- `backend/src/services/order_form_service.py`

### 実運用サンプルの週次シート構成

- `input_example/発注書/共通　2603.xlsx`
  - `3月1日～3月7日`
  - `3月8日～3月14日`
  - `3月15日～3月21日`
  - `3月22日～3月28日`
  - `3月29日～3月31日`
- `input_example/発注書/藍テラス　2604.xlsx`
  - `4月1日～4月4日`
  - `4月5日～4月11日`
  - `4月12日～4月18日`
  - `4月19日～4月25日`
  - `4月26日～4月30日`

このため、週次レンジは単純な「1日から7日刻み」ではなく、月と交差する `日曜-土曜` ブロックを採用しているとみなすのが妥当である。

### テンプレートの前提

- 2026-02-27時点の整理では、実運用帳票は6パターンに分類されている。
- 施設ごとに `fax_template_id` や `fax_template_override.columns` を持っており、列名や食種は施設設定から適用可能な構造になっている。

参照:
- `docs/order_form_template_check_20260227.md`
- `backend/src/data/facility_master.template.json`
- `backend/src/data/fax_templates.yaml`

## 目標アーキテクチャ

### 基本概念

生成物は以下の3層を前提にする。

1. ベースラインテンプレート
   - 帳票レイアウト、マーカー、ロゴ、固定罫線、所定欄
2. 施設テンプレート
   - ベースラインテンプレートに施設名、対象区分、施設固有ヘッダを適用したもの
3. 完成した発注書
   - 施設テンプレートに週次メニューを注入したもの

今回は、上記3層を内部責務として維持したまま、外部仕様としては「月1ファイルの完成した発注書」を返す。

### 生成フロー

1. Operator が `facility_id` と `month_id` を指定して生成を実行する
2. バックエンドが対象施設設定を解決する
3. 月次メニューを取得し、利用可能な献立行を正規化する
4. 対象月に対する週次レンジを生成する
5. 施設テンプレートをベースに、週ごとにシートを複製または生成する
6. 各週シートに該当日付の献立行のみを書き込む
7. メタデータシートを追加し、生成条件を記録する
8. 単一Workbookとして保存し、ダウンロードさせる

## 週次レンジ仕様

### 採用仕様

- 週次レンジは「月内と交差する日曜始まり、土曜終わり」とする
- 月初が日曜でない場合、先頭週は部分週とする
- 月末が土曜でない場合、末尾週は部分週とする
- シートには月外の日付は出さず、その月に属する日だけを表示する

### 例

- 2026-03
  - `3月1日～3月7日`
  - `3月8日～3月14日`
  - `3月15日～3月21日`
  - `3月22日～3月28日`
  - `3月29日～3月31日`
- 2026-04
  - `4月1日～4月4日`
  - `4月5日～4月11日`
  - `4月12日～4月18日`
  - `4月19日～4月25日`
  - `4月26日～4月30日`

### 実装上の注意

- 既存の `candidate_resolution_service._calendar_week_ranges_for_month()` は月初からの単純7日刻みであり、この仕様にはそのまま使えない
- 週次レンジ生成は、注文書生成専用の責務として `order_form_service` 側へ移すか、共有可能な別helperへ切り出す

## UI/API 設計

### フロントエンド

- 入力項目は現状維持
  - `施設`
  - `対象月`
  - 任意の `注文書パターン`
- 文言だけを実仕様に寄せる
  - 例: 「対象月の発注書を、週次シート付きで1ファイル生成します」
- 生成後メッセージには、対象月・シート数・施設名を含める

### バックエンドAPI

- エンドポイントは原則維持
  - `POST /order-forms/generate`
- 入力も原則維持
  - `facility_id`
  - `month_id`
  - `pattern_id` optional
- 返却形式もExcelファイル返却を維持する
- 将来的な監査のため、レスポンスヘッダまたはファイル名に `facility_id` と `month_id` を反映する

### APIの後方互換方針

- UI/呼び出し元は月入力のまま維持するため、API契約の破壊的変更は避ける
- 変更点は生成物の中身を「単一シートの汎用表」から「複数週シートのFAX帳票」へ切り替える点

## バックエンド設計

### 推奨責務分割

`order_form_service.py` に以下の責務を持たせる。

- `_normalize_month_id(month_id)`
- `_resolve_facility(facility_id)`
- `_resolve_pattern(facility, pattern_id)`
- `_collect_menu_entries(month_id, facility_id)`
- `_build_week_ranges_for_month(month_id)`
- `_format_week_sheet_name(start_date, end_date)`
- `_group_entries_by_week(entries, week_ranges)`
- `_build_facility_order_form_template(facility, pattern)`
- `_render_week_sheet(template_sheet, entries_for_week, week_range, facility, pattern)`
- `_build_metadata_sheet(workbook, facility, month_id, pattern, week_ranges, entry_count)`

### 生成方式

第一候補は以下。

- 実運用サンプルと整合するベースラインテンプレートを family ごとに保持する
- 施設設定でヘッダや施設名を適用し、施設テンプレート相当をメモリ上で構築する
- 月内の週数だけシートを複製して、週次メニューを注入する

これにより、以下を両立できる。

- 帳票レイアウトの一貫性
- 施設差分の適用
- 週次シートの量産

### 施設設定の適用

施設設定から適用する主な要素:

- `facility_name`
- `fax_template_id`
- `fax_template_override.columns`
- 施設に紐づく既定パターン

これにより、ベースラインテンプレートを増やしすぎず、施設差分は設定で吸収する。

## 出力仕様

### ファイル

- 1施設1か月につき1ファイル
- 推奨ファイル名:
  - `order_form_<facility_id>_<YYYY-MM>_<template>.xlsx`
  - もしくは `fax_order_form_<facility_id>_<YYYY-MM>.xlsx`

### シート

- 週ごとのシートを順番に配置する
- シート名は `3月22日～3月28日` のような実運用互換の形式を使う
- `設定` シートは hidden にし、以下を保持する
  - `generated_at`
  - `facility_id`
  - `facility_name`
  - `month_id`
  - `pattern_id`
  - `fax_template_id`
  - `sheet_count`
  - `entry_count`

### 所定欄

- 施設名は施設名枠に描画する
- 区分ヘッダは施設設定に従って描画する
- マーカー、ロゴ、固定文言はベースラインテンプレート準拠とする

## エラー処理と運用

### エラー条件

- `month_id` が不正
- `facility_id` が存在しない
- 月次メニューが存在しない
- 利用可能な献立行が0件
- 施設に `fax_template_id` が設定されていない
- ベースラインテンプレートが存在しない

### エラー方針

- 入力起因は `400`
- 想定外例外は `500`
- エラーメッセージは、運用で原因が判断できるように施設ID・対象月・欠損種別を含める

### ログ

- 生成開始/完了
- 対象施設、対象月
- 生成シート数
- 生成行数
- テンプレートID
- 警告件数

## 移行方針

### 段階的移行

1. 週次レンジ生成とシート名生成を実装する
2. 現行の `build_order_form_excel()` を複数シート出力へ差し替えるか、内部で新関数を呼ぶ
3. 既存のFAXテンプレ試作ロジックを、正式生成ロジックへ寄せる
4. 代表施設でGolden確認を行い、問題なければ既定動作に切り替える

### リスク低減

- 旧来の汎用1シート生成が必要なら、内部関数として当面残す
- ただしAPIは新仕様を既定とする
- 実帳票互換の確認が済むまでは、代表施設で先行検証する

## テスト戦略

### Unit テスト

対象:

- 月ID正規化
- 週次レンジ生成
- シート名整形
- 月次メニューの週振り分け
- パターン解決、施設解決、メタデータ作成

主要ケース:

1. `month_id` の正常/異常
2. 2026-03 のようなフル週開始月
3. 2026-04 のような月初部分週
4. 2月の月末部分週
5. 欠損日や重複日を含むメニュー行の振り分け
6. pattern override あり/なし

### Integration テスト

対象:

- `POST /order-forms/generate`
- `menu_service` と `config_service` を通じた生成
- Excel返却とメタデータシート

主要ケース:

1. メニューありで正常に複数シートWorkbookが返る
2. メニューなしで `400`
3. テンプレID未設定で `400`
4. 施設差分が異なる複数familyで生成できる

### Golden テスト

代表ケース:

- 3月サンプル
  - `3月1日～3月7日`
  - `3月8日～3月14日`
  - `3月15日～3月21日`
  - `3月22日～3月28日`
  - `3月29日～3月31日`
- 4月サンプル
  - `4月1日～4月4日`
  - `4月5日～4月11日`
  - `4月12日～4月18日`
  - `4月19日～4月25日`
  - `4月26日～4月30日`

確認項目:

- シート名一覧
- シート順序
- 週ごとの日付範囲
- 施設名欄と区分ヘッダ
- `設定` シートのキー行

### Manual テスト

1. 代表施設ごとに1件生成して、帳票の可読性を確認する
2. 実際のFAX送付前提で、シート名、ロゴ、所定欄、マーカー位置を確認する
3. 月初部分週と月末部分週で、日付表示や締切文言が崩れないことを確認する

## テスト実装方針

既存テスト:

- `backend/tests/unit/test_order_form_service.py`
- `backend/tests/integration/test_candidate_resolution_service.py`

追加候補:

- `backend/tests/unit/test_order_form_service.py`
  - 週次レンジ生成、週次シート名、複数シート生成のケースを追加
- `backend/tests/integration/test_order_forms_api.py`
  - APIからの複数シートWorkbook返却を確認
- Golden fixture 群
  - 代表施設・代表月の期待シート構成を固定

## 受け入れ条件

1. `POST /order-forms/generate` で、対象月の全週シートを含むExcelが返る
2. すべての月内日付が、重複なくいずれか1シートへ配置される
3. 3月・4月の既知サンプルと同じ週次レンジ構成を再現できる
4. 代表3テンプレ以上で生成に成功する
5. 既存 `test_order_form_service.py` の意図を壊さない
6. 手動確認で、実運用のFAX帳票として許容できる

## 未解決事項

- 発注書作成担当ユーザーが施設へ送る「40日前」の運用情報を、UI上の案内だけにするか、生成履歴や通知条件に組み込むか
- 週次シート内の `締切` 文言をどのロジックで正式管理するか
- PDF化やFAX送信をこの生成処理の後段へどう接続するか
- 3分割セル対応を、どのテンプレfamilyから先行導入するか

## 参照

- `frontend/src/pages/order-forms.tsx`
- `backend/src/api/order_forms.py`
- `backend/src/services/order_form_service.py`
- `backend/src/services/candidate_resolution_service.py`
- `backend/src/data/facility_master.template.json`
- `backend/src/data/fax_templates.yaml`
- `backend/tests/unit/test_order_form_service.py`
- `docs/order_form_template_check_20260227.md`
