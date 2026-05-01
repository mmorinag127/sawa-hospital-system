# OCR/注文書 拡張実装計画書（プレイスホルダー版）

## 目的
- 施設ごとの注文書フォーマット差異を吸収し、OCR精度と運用速度を上げる。
- OCR修正フローを現場運用に合わせて改善し、反映失敗を減らす。
- 佐川追跡表示、注文履歴DBなど周辺機能を追加し、運用の可視化を進める。
- 佐川の伝票管理Excelへ「到着日時」を自動入力し、難しい場合でも「全件配達完了フラグ」を可視化する。
- 特定施設のみOpenAI APIを使うOCRルートを追加し、難読フォーマットの認識精度を改善する。

## 前提
- 注文書パターン（4〜5種類）と施設割当は未受領。
- 本計画ではパターン定義をプレイスホルダーで固定し、後で値を埋める。

## 追加資料（2026-02-19受領）
- 佐川管理表サンプル:
  - `/Users/mmorinag/Sawa/2025.12/input_example/●sawa伝票番号管理表.xlsx`
- 依頼メール要旨:
  - 伝票番号（お問い合わせ送り状No.）から到着日時を調査し、添付Excelの「到着日時」へ自動入力したい。
  - もし到着日時の厳密自動入力が難しい場合は、全荷物の配達完了を通知するフラグ表示がほしい。

### サンプルExcel確認結果（要件化）
- シート:
  - `基`（テンプレート/集計ベース）
  - `2601`, `2602`（運用シート。月次で増える想定）
- 実データヘッダ（行2）:
  - 共通項目: `発送日`, `お届け先`, `伝票番号`, `到着日時`, `段ボールサイズ`, `重量`, `送料`
  - シートにより列順が異なるため、列インデックス固定は不可（ヘッダ名で列解決が必要）。
- 到着日時セルの形式:
  - 例: `02月18日 10時23分`

## スコープ
- 注文書フォーマット標準化（パターン管理）
- 注文書自動生成（週次メニュー連動）
- 歪み補正マーカー対応
- OCR修正UI（4分割セル、右側追記運用）
- OCR反映処理の堅牢化
- 大カッコ施設向け大セル対応
- 佐川追跡ステータス表示
- 佐川管理表Excelへの到着日時自動入力/配達完了フラグ
- 注文履歴DB（変更監査の可視化）
- 運用アカウント追加対応（張さん）
- OCR追加学習用データセット出力
- 特定施設向けOpenAI API OCR

## 注文書パターン定義（プレイスホルダー）
| pattern_id | 仮名称 | 対象施設 | レイアウト特性 | 補正マーカー | 大セル有無 | 備考 |
|---|---|---|---|---|---|---|
| PATTERN_A | 標準A | TBD | TBD | TBD | TBD | TBD |
| PATTERN_B | 標準B | TBD | TBD | TBD | TBD | TBD |
| PATTERN_C | 標準C | TBD | TBD | TBD | TBD | TBD |
| PATTERN_D | 標準D | TBD | TBD | TBD | TBD | TBD |
| PATTERN_E | 標準E | TBD | TBD | TBD | TBD | TBD |

## 実装方針（機能別）

### 1) 注文書フォーマット標準化（パターン管理）
#### 追加/修正対象
- Backend
  - `workspace/backend/src/services/config_service.py`
  - `workspace/backend/src/services/config_validator.py`
  - `workspace/backend/src/api/facilities.py`
  - `workspace/backend/src/api/facility_master.py`
- Frontend
  - `workspace/frontend/src/pages/facilities/[id].tsx`
  - `workspace/frontend/src/pages/facility-master.tsx`

#### 実装内容
- 施設設定に `order_form_pattern_id` を追加（JSON設定で管理）。
- パターン共通定義をマスター側に保持し、施設側はID参照にする。
- UIで施設ごとにパターンを選択可能にする。

#### 受け入れ条件
- 施設詳細画面で `PATTERN_A〜E` を選択・保存できる。
- OCR解析時に施設パターン設定が参照される。

### 2) 注文書自動生成（週次メニュー連動）+ マーカー埋め込み
#### 追加/修正対象
- Backend
  - `workspace/backend/src/services/order_form_service.py`（新規）
  - `workspace/backend/src/api/order_forms.py`（新規）
  - `workspace/backend/src/services/menu_service.py`
  - `workspace/backend/src/main.py`
- Frontend
  - `workspace/frontend/src/pages/order-forms.tsx`（新規）
  - `workspace/frontend/src/components/TopNav.tsx`

#### 実装内容
- 施設・週指定で注文書テンプレートを自動生成（Excel/PDF）。
- パターンごとにマーカー配置ルールを切替。
- 生成物のダウンロード導線を追加。

#### 受け入れ条件
- 画面操作のみで施設別注文書を生成・ダウンロードできる。
- 出力帳票にマーカーが反映される。

### 3) OCR修正UI改善（4分割セル/右側追記）+ OCR反映堅牢化
#### 追加/修正対象
- Frontend
  - `workspace/frontend/src/pages/orders/[id].tsx`
- Backend
  - `workspace/backend/src/api/orders.py`
  - `workspace/backend/src/services/order_service.py`
  - `workspace/backend/src/services/fax_extractor.py`
  - `workspace/backend/src/services/fax_parser.py`

#### 実装内容
- OCR編集テーブルでセル分割入力を扱えるUIに変更。
- 「OCRテーブルを反映」をMarkdown依存だけでなく構造化データ反映でも処理可能にする。
- 反映失敗時に原因（`rows_empty` など）を画面へ明示。

#### 受け入れ条件
- 既存「編集対象: Page X」からの反映成功率が改善する。
- 失敗時、再現可能な理由が画面に表示される。

### 4) 大カッコ施設向け大セル対応
#### 追加/修正対象
- Backend
  - `workspace/backend/src/services/fax_parser.py`
  - `workspace/backend/src/services/config_validator.py`
  - `workspace/backend/src/data/fax_templates.yaml`
- Frontend
  - `workspace/frontend/src/pages/ocr-templates.tsx`

#### 実装内容
- パターン/施設単位で「大セルモード」を切替可能にする。
- 大セル行の分割・数量抽出ルールを別系統で適用。

#### 受け入れ条件
- 大カッコ施設で、通常セルモードより欠損/誤読が減る。

### 5) 佐川追跡ステータス表示
#### 追加/修正対象
- Backend
  - `workspace/backend/src/services/sagawa_tracking_service.py`（新規）
  - `workspace/backend/src/api/shipping.py`
  - `workspace/backend/src/services/shipping_service.py`
- Frontend
  - `workspace/frontend/src/pages/shipping.tsx`

#### 実装内容
- 抽出済み伝票番号に対して追跡ステータスを取得。
- 配送状況・最終更新時刻をUI表示。
- 管理表Excel（`●sawa伝票番号管理表.xlsx` 形式）を入力し、`伝票番号` 列を基に `到着日時` 列を更新して再出力する。
- シートごとにヘッダ行（行2想定）から列名で位置解決し、列順差異（`2601`/`2602`）を吸収する。
- 優先仕様:
  - 配達完了（`配達完了` / `お届け済み`）なら到着日時を自動入力。
  - 未完了は空欄維持。
- 代替仕様（依頼メールのfallback要件）:
  - 全件が配達完了なら `全件配達完了フラグ=true` を返す/表示する。
  - 出力Excelにも「完了フラグ」列（未存在時は追加）を付与可能にする。

#### API案
- `POST /shipping/track-status`
  - 入力: 伝票番号リスト
  - 出力: 伝票番号ごとの配送ステータス・到着日時
- `POST /shipping/enrich-excel`
  - 入力: 佐川管理表Excel
  - 出力: 到着日時更新済みExcel + 集計（完了件数/未完了件数/全件完了フラグ）

#### 受け入れ条件
- 伝票番号ごとに追跡状態が一覧表示される。
- `●sawa伝票番号管理表.xlsx` 形式で、到着日時の自動埋め戻しが動作する。
- 追跡不能ケースでも、全件完了フラグ判定を返せる。

### 6) 注文履歴DB（変更監査の可視化）
#### 追加/修正対象
- Migration
  - `workspace/backend/migrations/0006_order_history.py`（新規）
- Backend
  - `workspace/backend/src/models/order_history.py`（新規）
  - `workspace/backend/src/services/order_history_service.py`（新規）
  - `workspace/backend/src/api/order_history.py`（新規）
  - `workspace/backend/src/services/order_service.py`
  - `workspace/backend/src/main.py`
- Frontend
  - `workspace/frontend/src/pages/orders/[id].tsx`

#### 実装内容
- 注文の主要操作（施設設定、OCR反映、明細更新、確定）を履歴保存。
- 注文詳細で時系列表示。

#### 受け入れ条件
- 各注文で「いつ、誰が、何を変えたか」を追跡できる。

### 7) 運用アカウント追加（張さん）
#### 追加/修正対象
- Backend
  - `workspace/backend/src/api/auth.py`（許可メール設定運用）
- 運用
  - Googleアカウント作成、許可リスト登録

#### 実装内容
- OCR確認業務用アカウントを追加し、アクセス制御を設定。

#### 受け入れ条件
- 張さんがOCR確認画面へログインし、必要機能のみ利用できる。

### 8) OCR追加学習用データセット出力
#### 追加/修正対象
- Backend
  - `workspace/backend/src/services/order_service.py`
  - `workspace/backend/src/api/orders.py`
  - `workspace/backend/src/services/ocr_registry_service.py`

#### 実装内容
- 確定済み注文から「画像+OCR+確定値」ペアをエクスポート。
- モデル学習に渡せる形式（JSONL/CSV+参照URI）で出力。

#### 受け入れ条件
- 1年分データを再現可能な形式で抽出できる。

### 9) 特定施設向け OpenAI API OCR
#### 追加/修正対象
- Backend
  - `workspace/backend/src/services/openai_ocr_service.py`（新規）
  - `workspace/backend/src/services/fax_extractor.py`
  - `workspace/backend/src/services/order_service.py`
  - `workspace/backend/src/services/config_service.py`
  - `workspace/backend/src/services/config_validator.py`
  - `workspace/backend/src/api/facilities.py`
- Frontend
  - `workspace/frontend/src/pages/facilities/[id].tsx`
  - `workspace/frontend/src/pages/ocr-templates.tsx`

#### 実装内容
- 施設設定に以下を追加:
  - `main_ocr_provider: openai|pipeline`
  - `openai_ocr_enabled: true|false`
  - `openai_ocr_model`（例: `gpt-4.1-mini` / `gpt-4.1` など）
  - `openai_ocr_prompt`（施設固有プロンプト）
- `main_ocr_provider=openai` かつ対象施設の場合のみOpenAI API経由でOCRを実行。
- 出力は既存パーサ互換の構造（`rows` / `table_raw`）へ正規化し、既存処理へ接続。
- 失敗時は既存パイプラインにフォールバック可能にする（運用停止回避）。
- コストと遅延を制御するため、対象施設限定で段階導入する。

#### 受け入れ条件
- 指定施設のみOpenAI OCRが有効化される。
- 既存施設の挙動は変わらない。
- OpenAI OCR失敗時も処理全体が停止しない（フォールバック動作）。

## 実施フェーズ
### Phase 1（先行）
- パターン管理
- 注文書自動生成
- 佐川管理表Excelの到着日時自動入力（MVP）

### Phase 2
- OCR修正/反映堅牢化
- 大セル対応
- 佐川追跡表示（UI強化・完了フラグ表示）
- OpenAI OCR（特定施設パイロット）

### Phase 3
- 注文履歴DB
- 運用アカウント整備
- OCR学習データ出力
- OpenAI OCR対象施設の段階拡大

## タスク一覧（チェックリスト）
- [x] パターン定義スキーマ追加（`PATTERN_A〜E`）
- [x] 施設設定UIにパターン選択追加
- [x] 注文書生成API実装
- [x] 注文書生成画面追加
- [x] マーカー埋め込みルール実装
- [ ] OCR編集UIを4分割運用へ変更
- [x] OCR反映APIを構造化入力対応に拡張
- [x] 反映失敗メッセージの詳細化
- [ ] 大セルモード追加
- [x] 佐川追跡API連携（伝票番号→ステータス/到着日時）
- [x] 佐川管理表Excelのヘッダ解析（列順差異吸収）
- [x] 佐川管理表Excelへの到着日時埋め戻し
- [x] 全件配達完了フラグ判定/表示
- [x] 追跡状態UI表示
- [ ] 注文履歴テーブル/モデル/API追加
- [ ] 注文詳細に履歴表示追加
- [ ] OCR学習データ出力機能追加
- [x] OpenAI OCRサービス実装（施設限定ルーティング）
- [x] OpenAI OCR設定UI追加（施設別）
- [x] OpenAI OCRフォールバック/コスト制御実装
- [ ] 受け入れテスト（主要操作のE2E）

## 依存情報（受領待ち）
- 注文書パターン確定情報
  - `PATTERN_A` 対象施設: TBD
  - `PATTERN_B` 対象施設: TBD
  - `PATTERN_C` 対象施設: TBD
  - `PATTERN_D` 対象施設: TBD
  - `PATTERN_E` 対象施設: TBD
- 大カッコ施設リスト: TBD
- 既存注文書PDF（1年分）: TBD
- 佐川追跡連携仕様/認証情報:
  - 公式API利用可否: TBD
  - Web追跡利用時のアクセス制約（レート/認証）: TBD
- 張さんの利用メールアドレス: TBD
- OpenAI OCR対象施設リスト: TBD
- OpenAI API接続情報（`OPENAI_API_KEY`、利用モデル）: TBD

## リスクと対策
- パターン定義遅延: プレイスホルダーIDで先に実装し、後から施設割当を投入。
- OCR反映不整合: Markdownと構造化入力の二経路を用意し、どちらかで回復可能にする。
- 追跡API制限: レート制御とキャッシュを前提に設計する。
- 佐川管理表フォーマット差異: ヘッダ名検出で列を解決し、列位置固定を禁止する。
- OpenAI OCRコスト増: 対象施設限定、最大ページ数制限、リトライ回数制限で制御する。
- OpenAI OCR応答揺れ: JSONスキーマ検証 + 既存OCRへのフォールバックを必須化する。

## 変更管理
- 本書は改訂版。パターン情報受領後に `v1.2` を発行して確定値を反映する。

---

## 追加方針（2026-02-20）
### 11) シート型OCR編集UI（Excel/Sheets代替）
#### 目的
- OCR修正をMarkdown中心の運用から、運用者が直感的に扱える「シート型UI」へ移行する。
- FAX PDFを横に表示しながら同一画面で修正できるようにする。
- 既存OCR修正UIは削除せず、`legacy` オプションとして残す。

#### 実装方針
- Frontend `orders/[id]` に編集UIモードを追加:
  - `sheet`（新UI、デフォルト）
  - `legacy`（既存UI）
- `sheet` では以下を提供:
  - 行番号付きグリッド編集
  - 週次メニュー順/施設区分列を前提にした固定列表示（段階導入）
  - OCR結果のセル直接編集
  - 既存の `OCRテーブルを反映` をそのまま利用
- Backendは既存 `POST /orders/{id}/ocr-apply`（構造化 rows/header）を継続利用し、反映結果を現行フローへ接続する。

#### 段階導入
1. UI切替（sheet/legacy）とシート型編集画面を先行実装
2. 週次メニュー+施設パターンからの「事前整形シート」生成ロジックを追加
3. OCR書き込みを行キー（`row_id` 等）ベースに切替し、位置ズレ耐性を上げる
4. 反映履歴（raw OCR / sheet edited OCR）参照を追加

#### 受け入れ条件
- 運用者がPDFを見ながら同一画面で修正できる
- 修正結果を既存の注文明細反映フローに適用できる
- `legacy` UIへいつでも切り替え可能
