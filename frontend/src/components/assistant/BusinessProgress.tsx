export type BusinessProgress = {
  state:"source"|"question"|"review"|"working"|"checking"|"completed"|"archived";
  title:string;
  message:string;
  next_action:string;
  deliverables?:string[];
};

export const businessLabels:Record<BusinessProgress["state"],string> = {
  source:"原本の添付待ち",question:"回答待ち",review:"内容の確認待ち",working:"作業中",
  checking:"システム側で確認中",completed:"完了",archived:"過去の記録",
};

// Older API responses may be retained while a new revision is rolling out.
// They can display a record, but can never grant a new execution or approval.
export function businessProgress(run:{business?:BusinessProgress;status:string;pending_question?:unknown;task_review?:{state:string}}):BusinessProgress {
  if(run.business)return run.business;
  if(run.status==="completed")return {state:"completed",title:"作業が完了しました",message:"保存した内容と完成ファイルを確認できます。",next_action:"outputs"};
  return {state:"checking",title:"作業の内容を確認しています",message:"回答・承認した内容は保持されています。操作は必要ありません。システム側で確認しています。",next_action:"none"};
}
