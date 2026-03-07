import Link from "next/link";

type Props = {
  className?: string;
};

export default function GmailInvalidGrantRecoverySteps({ className }: Props) {
  return (
    <div className={className}>
      <p>UIのみ（コマンド不要）の復旧手順はこちらです。</p>
      <p>
        <Link href="/system-recovery-runbook">/system-recovery-runbook</Link>
      </p>

      <p>
        これで直らない場合は refresh_token が revoke されているか、OAuth クライアント /
        スコープ / 対象アカウントが違う可能性が高いので、1 から取り直してください。
      </p>

      <style jsx>{`
        a {
          color: #0f5b8f;
          text-decoration: underline;
          word-break: break-all;
        }

        p {
          margin: 0 0 10px;
          line-height: 1.55;
        }

        code {
          background: rgba(31, 42, 42, 0.08);
          border-radius: 6px;
          padding: 1px 6px;
        }
      `}</style>
    </div>
  );
}
