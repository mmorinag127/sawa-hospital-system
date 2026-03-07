import type { ReactNode } from "react";

// NOTE: The runbook text below is intentionally rendered verbatim.
// URLs are turned into clickable anchors while keeping the visible text unchanged.
const RUNBOOK_TEXT = `この状態（\`Gmail watch: invalid_grant\` と \`OCRパイプライン: URL=NG\`）は、下の **UI手順だけ**で復帰できます（CLI/コマンド不要）。

重要: \`refresh_token\` は機密です。ブラウザの入力欄以外（チャット等）に貼らないでください。

---

## 1) Gmail Watch 復帰（\`invalid_grant\` を消す）

### 1-0. 環境を固定
1. Cloud Console を開き、プロジェクトが **\`sawahospitalsystem\`** になっていることを確認する。

### 1-1. まず「7日で再発する設定」になっていないか確認（超重要）
\`invalid_grant\` が周期的に出る典型原因です。
1. Cloud Console 左メニュー →「API とサービス」→「OAuth 同意画面」を開く。
2. 「公開ステータス / Publishing status」が **テスト**になっていたら、可能なら **本番（In production）**へ変更する。  
   変更できない場合: 以後も定期的に refresh_token が失効し得るため、今回直しても再発します（運用が要ります）。

### 1-2. OAuth Playground が使える redirect URI を追加（不足すると途中で詰みます）
1. \`https://console.cloud.google.com/apis/credentials?project=sawahospitalsystem\` を開く。
2. 「OAuth 2.0 クライアント ID」→ **\`ウェブ クライアント 1\`** を開く。
3. 「承認済みのリダイレクト URI」に以下が **全部**あることを確認し、無ければ追加する。
4. \`http://127.0.0.1:3100/api/auth/callback\`
5. \`http://localhost:3100/api/auth/callback\`
6. \`https://developers.google.com/oauthplayground\`
7. 「保存」を押す。

### 1-3. OAuth Playground で refresh_token を “必ず” 取り直す
1. \`https://developers.google.com/oauthplayground/\` を開く。
2. 右上の歯車（設定）を開き、以下を設定する。
3. 「Use your own OAuth credentials」= ON
4. Client ID / Client secret に、さっきの **\`ウェブ クライアント 1\`** の値をコピペ（同じ画面内に表示されています）。
5. 「Access type」= **Offline**
6. 「Approval prompt」= **Force**（同意画面を強制。refresh_token を確実に出すため）
7. 左のスコープ一覧で Gmail API の **\`https://mail.google.com/\`** を選択 →「Authorize APIs」。
8. **FAXを受ける対象Gmailアカウント**でログイン → 同意で「許可」。
9. Step 2 の「Exchange authorization code for tokens」を押す。
10. 返ってきた JSON の **\`refresh_token\`** をコピーする。  
    \`refresh_token\` が出ない場合: 設定の「Approval prompt=Force」になっているか確認し、ブラウザのシークレットウィンドウでやり直す（同意済みキャッシュが残ると出ないことがあります）。

### 1-4. Secret Manager の \`gmail-refresh-token\` を更新
1. \`https://console.cloud.google.com/security/secret-manager/secret/gmail-refresh-token/versions?project=sawahospitalsystem\` を開く。
2. 「新しいバージョンを追加」を押す。
3. シークレット値に **refresh_token だけ**を貼り付ける（引用符や説明文は入れない）。
4. 「追加」を押す。

### 1-5. Worker を再デプロイして secret の新バージョンを確実に反映
（Secret を更新しても、稼働中のリビジョンは自動反映されない前提で動くのが安全です）
1. Cloud Console →「Cloud Run」→ Worker（URL が \`https://worker-prod-avlnzjjrca-dt.a.run.app\` のサービス）を開く。
2. 「編集して新しいリビジョンをデプロイ」を押す。
3. 「変数とシークレット」で、Gmail の refresh token が Secret Manager の **\`gmail-refresh-token\`** を参照していることを確認する。
4. 「デプロイ」を押す。

### 1-6. Watch を再登録（Cloud Scheduler を UI で手動実行）
1. \`https://console.cloud.google.com/cloudscheduler/jobs?project=sawahospitalsystem\` を開く。
2. リージョンを **\`asia-northeast2\`** にする。
3. ジョブ **\`gmail-watch-refresh-prod\`** を開く（または一覧で選択）。
4. 「今すぐ実行（Run now）」を押す。
5. 「実行履歴」で最新が **成功**であることを確認する。  
   失敗なら「詳細」に HTTP ステータスが出るので、以下で直します。
6. 401/403 の場合: Cloud Run の Worker →「権限」で、この Scheduler が使うサービスアカウントに **Cloud Run Invoker** が付いているか確認し、無ければ追加する。
7. 400 で \`invalid_grant\` が出る場合: refresh_token がまだ不正（別アカウント/別クライアント/テスト運用等）。1-1〜1-3 をやり直す。

### 1-7. 復帰確認（Web）
1. \`https://web-prod-avlnzjjrca-dt.a.run.app\` を開く（別URLでもこのURLへリダイレクトされます）。
2. 「システム状態」等で Gmail Watch を確認し、以下になっていれば復帰完了。
3. \`エラー: invalid_grant\` が消えている
4. \`有効期限\` が **未取得ではない**（日時が出る）

---

## 2) OCR パイプライン復帰（\`URL=NG\` を \`OK\` にする）

この環境では OCR は **GCS（バケット）経由でトリガーされる運用**なので、基本は Bucket が正しければ動きます。  
（\`OCR_PIPELINE_URL\` は “HTTPで起動したい場合” のオプションで、未設定でも OCR は止まりません）

### 2-1. まず Bucket 設定だけ確認（最優先）
1. Web の「システム状態」で OCR パイプラインの設定が以下になっていることを確認する。
2. \`Bucket=OK\`
3. \`Bucket: sawahospitalsystem-prod-raw / input=input/ / output=output/\`

### 2-2. OCR が本当に止まっている時だけ（Cloud Run / トリガー確認）
1. Cloud Console →「Cloud Run」→ **\`ocr-pipeline-prod\`** の「ログ」を開く。
2. 新しいPDF取込があるのにログが全く増えない場合: Storage のトリガー（Eventarc/通知）が死んでいる可能性があります。
3. その場合は一旦ここで止めてください（設定を変えると復旧はできますが、権限/トリガー設計の確認が必要です）。

### 2-3. （参考）\`OCR_PIPELINE_URL\` を使う場合
\`OCR_PIPELINE_URL\` を入れると Worker が \`ocr-pipeline-prod\` に HTTP POST します。  
この場合、\`ocr-pipeline-prod\` が認証必須だと 403 になるため、**認証付き呼び出しの実装**が必要です（簡単ではありません）。

### 2-4. 復帰確認（Web）
1. Web の「システム状態」で OCR パイプラインが以下になっていれば設定復帰です。
2. \`設定: Bucket=OK\`
3. その後 \`processing\` が動かない場合は \`ocr_pipeline.last_error\` が更新されるので、その文言に沿って次の手当（403/404/500 等）をします。

---

## 3) Web OAuth（ログインで \`origin_mismatch\` が出た時だけ）
1. \`https://console.cloud.google.com/apis/credentials?project=sawahospitalsystem\` → \`ウェブ クライアント 1\`
2. 「承認済みの JavaScript 生成元」に以下があることを確認（両方あるのが安全）。
3. \`https://web-prod-avlnzjjrca-dt.a.run.app\`
4. \`https://web-prod-167795504375.asia-northeast2.run.app\`
5. 保存 → シークレットウィンドウで \`https://web-prod-avlnzjjrca-dt.a.run.app/login\` を開き直す

---`;

const URL_RE_SOURCE = "https?://[^\\\\s`]+";

function linkify(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  // Create a fresh regex instance because /g regex is stateful.
  const re = new RegExp(URL_RE_SOURCE, "g");

  while ((match = re.exec(text)) !== null) {
    const url = match[0];
    const start = match.index ?? 0;
    if (start > lastIndex) nodes.push(text.slice(lastIndex, start));
    nodes.push(
      <a key={`${start}:${url}`} href={url} target="_blank" rel="noreferrer noopener">
        {url}
      </a>,
    );
    lastIndex = start + url.length;
  }

  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export default function SystemRecoveryRunbookPage() {
  return (
    <main className="page">
      <pre className="runbook">{linkify(RUNBOOK_TEXT)}</pre>
      <style jsx>{`
        .page {
          min-height: 100vh;
          padding: 24px 6vw 80px;
          background: #fbfaf7;
        }

        .runbook {
          background: #ffffff;
          border: 1px solid rgba(31, 42, 42, 0.14);
          border-radius: 18px;
          padding: 18px 18px;
          font-size: 14px;
          line-height: 1.65;
          white-space: pre-wrap;
          overflow-wrap: anywhere;
        }

        .runbook :global(a) {
          color: #0f5b8f;
          text-decoration: underline;
          word-break: break-all;
        }
      `}</style>
    </main>
  );
}
