import { Fragment, FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { apiClient } from "../services/apiClient";
import type { AxiosRequestConfig } from "axios";
import DrawingQuestion, {QuestionMap} from "../components/assistant/DrawingQuestion";
import ResultView from "../components/assistant/ResultView";
import {businessProgress, businessLabels, type BusinessProgress} from "../components/assistant/BusinessProgress";
import ShiftReview, {ShiftReviewData} from "../components/assistant/ShiftReview";
import {mergeAssistantHistory,type AssistantEventPage} from "../services/assistantProgress";
import useAssistantHistory, {type AssistantRunSummary} from "../features/assistant/useAssistantHistory";
import RunEventHistory from "../components/assistant/RunEventHistory";

const BUSINESS_TIMEOUT = 300000;
const terminalRun = (status: string) => ["completed", "rolled_back", "stopped", "failed"].includes(status);
const assistantApi = {
  post<T=any>(url:string,data?:unknown,options?:AxiosRequestConfig) {
    const business=/^\/assistant-trial\/runs\/[^/]+\/(?:(?:reply|decision|resume|stop|rollback|refresh-usage)|faxes\/[^/]+\/decision)$/.test(url);
    return apiClient.post<T>(url,data,business ? {...options,timeout:BUSINESS_TIMEOUT} : options);
  },
};

type Task = { requires_agent?:boolean; id: string; domain: string; label: string; description: string; steps: string[]; target_kind?: string; month?: boolean; date?:boolean; default_month?: string };
type Fax = { fax_id: string; state: string; snapshot: { name: string; facility: string; week: string }; decision?: { message: string }; review?: { id: string; summary: string; sheet: { header?: any[]; fields?: any[]; rows?: any[][] }; original: { id: string; name: string } } };
type Selection = { inputs?:Record<string,any>; month_choices?:{month:string;label:string;reason:string}[]; batches?:{label:string;keys:string[];reason:string}[]; absence_days?:{date:string;label:string}[]; selection_id: string; choices: {key:string;label:string;data:Record<string,any>}[]; schools:{id:number;name:string}[]; layouts:{id:number;name:string;version:number}[]; work_days?:{service_date:string;menu_names:string[];menu_count:number;reason:string;missing:string[]}[] };
type TaskReview = {id:string;state:string;summary:string;evidence:{operation:string;data:any}[];actions:{operation:string;parameters:any}[];decision?:{approve:boolean;message:string}};
type ChatMessage = {question_map?:QuestionMap;id:string;role:"assistant"|"user";text:string;question_id?:string;options?:string[];process_context?:{answered:number;total:number;kind?:string}};
type Run = { business?:BusinessProgress; business_messages?:ChatMessage[]; request_key?:string; provider?:string; usage?:{estimated?:boolean}; lunch_review?:Record<string,string>[]; shift_review?:ShiftReviewData; conversation?:ChatMessage[]; pending_question?:ChatMessage; task_review?:TaskReview; task_inputs?:{inputs?:Record<string,any>;targets?:{label:string;data:Record<string,any>}[]}; task_id?: string; fax_items?: Fax[]; id: string; instruction: string; domains: string[]; status: string; cap: number; spent: number;
  events?: { id: number; kind: string; payload: Record<string, any> }[];
  source_attached?:boolean;
  event_page?:AssistantEventPage;
  fax_page?:{next_after:number|null;state:string;summary:{total:number;states:Record<string,number>}};
  artifact_page?:{next_after:number|null};
  artifacts?: { id: string; name: string; size: number }[] };
type Budget = { monthly_cents: number; spent_cents: number; reserved_cents: number; available_cents: number; notice: string };
type Configuration = { provider_id?:string; provider?:string; providers?:{id:string;label:string;missing:string[]}[]; missing: string[]; domains: Record<string, { configured: boolean; baseline_ready: boolean; data?: string }>; budget: Budget };
const labels: Record<string, string> = { hospital: "病院注文", shift: "シフト", "school-lunch": "学校給食" };
const states: Record<string, string> = { preparing: "準備中", running: "処理中", waiting_input: "確認待ち", completed: "完了", stopping: "停止中", stopped: "停止済み", rolled_back: "元に戻しました", failed: "準備失敗", reconciliation_required: "結果の照合が必要", stop_pending: "業務停止済み・接続先の停止確認中", rollback_stop_pending: "復元済み・接続先の停止確認中", budget_reached: "予算到達" };
const dollars = (cents: number) => `$${(cents / 100).toFixed(2)}`;
const cents = (text: string) => {
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error("金額は小数点以下2桁までで入力してください");
  const [whole, fraction = ""] = text.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
};

export function AssistantScreen({adminView=false}:{adminView?:boolean}) {
  const targetRequest=useRef(0);
  const pageRef=useRef<HTMLElement>(null);
  const selectedId=useRef<string|undefined>(undefined);
  const pendingRequests=useRef(0);
  const reconcilingCreate=useRef(false);
  const progressRequest=useRef(0);
  const progressAlive=useRef(true);
  useEffect(()=>{progressAlive.current=true;return()=>{progressAlive.current=false;++progressRequest.current;};},[]);
  const progressRecord=useRef<Run|null>(null);
  const resources=useRef({id:'',fax:0,artifact:0,state:''});
  const [faxBack,setFaxBack]=useState<number[]>([]);
  const [artifactBack,setArtifactBack]=useState<number[]>([]);
  const [choosingTask,setChoosingTask]=useState(true);
  const [loadingTargets,setLoadingTargets]=useState(false);
  const [config, setConfig] = useState<Configuration | null>(null);
  const history=useAssistantHistory();
  const runs=history.items;
  const [selected, setSelected] = useState<Run | null>(null);
  const [domain, setDomain] = useState("hospital");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskId, setTaskId] = useState("");
  const [corrections, setCorrections] = useState<Record<string, string>>({});
  const [targetMonth,setTargetMonth]=useState("");
  const [serviceDate,setServiceDate]=useState("");
  const [absenceDate,setAbsenceDate]=useState("");
  const [schoolId,setSchoolId]=useState("");
  const [selection,setSelection]=useState<Selection|null>(null);
  const [targetKeys,setTargetKeys]=useState<string[]>([]);
  const [layoutId,setLayoutId]=useState("");
  const [reviewMessage,setReviewMessage]=useState("");
  const [month, setMonth] = useState("");
  const [reply, setReply] = useState("");
  const [error, setError] = useState("");
  const [pollNotice, setPollNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [controlling,setControlling]=useState(false);
  const [controlNotice,setControlNotice]=useState("");
  const [pendingReply,setPendingReply]=useState<{runId:string;questionId?:string}|null>(null);
  const [pendingDecision,setPendingDecision]=useState<{runId:string;reviewId:string;faxId?:string}|null>(null);
  const [confirmRollback,setConfirmRollback]=useState(false);
  const [pendingCreate,setPendingCreate]=useState<string|null>(null);
  const [requestKey, setRequestKey] = useState("");
  const [source, setSource] = useState<File | null>(null);
  const [sourceChecked, setSourceChecked] = useState(false);

  function receiveRun(run: Run) {
    setSelected(run);
    history.patch(run);
    setPendingReply(pending=>pending?.runId===run.id && (run.status!=="waiting_input" || run.pending_question?.question_id!==pending.questionId) ? null : pending);
    setPendingDecision(pending=>{
      if(!pending || pending.runId!==run.id)return pending;
      const fax=pending.faxId ? run.fax_items?.find(fax=>fax.fax_id===pending.faxId) : undefined;
      const review=pending.faxId ? fax?.review : run.task_review;
      const state=pending.faxId ? fax?.state : run.task_review?.state;
      return ["completed","stopped","rolled_back","rollback_stop_pending","failed","reconciliation_required"].includes(run.status)
        || review && review.id!==pending.reviewId || state && state!=="awaiting_approval" ? null : pending;
    });
  }

  async function readRun(id:string) {
    if(resources.current.id!==id){resources.current={id,fax:0,artifact:0,state:''};setFaxBack([]);setArtifactBack([]);}
    const ticket=++progressRequest.current;
    const previous=progressRecord.current?.id===id?progressRecord.current:null;
    let data:Run;
    try {
      const response=await apiClient.get<Run>(`/assistant-trial/runs/${id}`,{
        params:{events_after:previous?.event_page?.next_event ?? 0,faxes_after:resources.current.fax,
          fax_state:resources.current.state,artifacts_after:resources.current.artifact},
      });
      data=response.data;
    } catch(error) {
      if(!progressAlive.current || selectedId.current!==id || ticket!==progressRequest.current)return;
      throw error;
    }
    if(!progressAlive.current || selectedId.current!==id || ticket!==progressRequest.current)return;
    const merged={...data,events:mergeAssistantHistory(previous?.events,data.events),
      conversation:mergeAssistantHistory(previous?.conversation,data.conversation),
      business_messages:mergeAssistantHistory(previous?.business_messages,data.business_messages)};
    progressRecord.current=merged;receiveRun(merged);
  }

  async function moveResources(kind:'fax'|'artifact', direction:'next'|'previous'|'reset', state?:string) {
    if(!selected)return;
    const old=resources.current, stack=kind==='fax'?faxBack:artifactBack;
    const next=kind==='fax'?selected.fax_page?.next_after:selected.artifact_page?.next_after;
    if(direction==='next'&&next==null || direction==='previous'&&!stack.length)return;
    const cursor=direction==='next'?next!:direction==='previous'?stack[stack.length-1]:0;
    const revised={...old,[kind]:cursor,...(state===undefined?{}:{state})};
    const revisedStack=direction==='next'?[...stack,old[kind]]:direction==='previous'?stack.slice(0,-1):[];
    const setBack=kind==='fax'?setFaxBack:setArtifactBack;
    resources.current=revised;setBack(revisedStack);
    try {await readRun(selected.id);}
    catch(error){if(resources.current===revised){resources.current=old;setBack(stack);}throw error;}
  }

  async function load(id?: string) {
    const configuration = apiClient.get<Configuration>("/assistant-trial/config").then(response => setConfig(response.data));
    await Promise.all([
      configuration,
      history.refresh(),
      apiClient.get<Task[]>("/assistant-trial/tasks").then(async response => { await configuration; setTasks(response.data); }),
      ...(id ? [readRun(id)] : []),
    ]);
  }
  async function reconcileCreate(key:string) {
    if(reconcilingCreate.current)return;
    reconcilingCreate.current=true;
    try {
    const response=await apiClient.get<{run:Run|null}>("/assistant-trial/run-lookup",{params:{request_key:key}});
    const run=response.data.run;
    if(!run)return;
    history.patch(run);
    if(selectedId.current!==run.id){selectedId.current=run.id;setSelected(run);}
    setPendingCreate(null);setRequestKey("");
    await refreshAfterCommand(run.id);
    } finally {reconcilingCreate.current=false;}
  }
  useEffect(()=>{
    if(!pendingCreate)return;
    let active=true,polling=false;
    const timer=setInterval(async()=>{
      if(!active || polling || document.hidden)return;
      polling=true;
      try {await reconcileCreate(pendingCreate);}
      catch {if(active)setPollNotice("受付状況を再確認しています。同じ依頼は再送していません。");}
      finally {polling=false;}
    },5000);
    return ()=>{active=false;clearInterval(timer);};
  },[pendingCreate]);
  async function refreshAfterCommand(id: string) {
    try { await load(id); setPollNotice(""); }
    catch { setPollNotice("進捗を取得できませんでした。再確認しています。作業は再送していません。"); }
  }
  useEffect(() => {
    let active=true,timer:ReturnType<typeof setTimeout>;
    async function initialLoad(){
      try {await load();if(active)setPollNotice("");}
      catch(e:any){
        if(!active)return;
        if(e.response && e.response.status<500){showError(e);return;}
        setPollNotice("作業一覧を再確認しています。業務操作は再送していません。");
        timer=setTimeout(initialLoad,5000);
      }
    }
    void initialLoad();
    return ()=>{active=false;clearTimeout(timer);};
  }, []);
  useEffect(()=>{
    if(adminView || selected || !runs.some(run => !terminalRun(run.status)))return;
    let active=true,polling=false;
    const timer=setInterval(async()=>{
      if(polling || document.hidden || history.loading)return;
      polling=true;
      try{if(active)await history.refresh();}
      catch{if(active)setPollNotice("作業の進み具合を確認しています。回答した内容は保持されています。");}
      finally{polling=false;}
    },2500);
    return()=>{active=false;clearInterval(timer);};
  },[selected?.id,adminView,runs.some(run => !terminalRun(run.status)),history.refresh,history.loading]);
  useEffect(() => {
    setPollNotice("");
    if (!selected) return;
    let active = true;
    const id = selected.id;
    if (terminalRun(selected.status)) {
      if (adminView) void apiClient.get<Budget>("/assistant-trial/budget")
        .then(({data}) => { if (active && selectedId.current === id) setConfig(c => c ? {...c, budget: data} : c); })
        .catch(() => { if (active) setPollNotice("利用料金を取得できませんでした。"); });
      return () => { active = false; };
    }
    let polling = false;
    const timer = setInterval(async () => {
      if (polling || document.hidden || pendingRequests.current>0) return;
      polling = true;
      try {
        await readRun(id);
        if (active && selectedId.current===id) setPollNotice("");
        if (adminView) {
          try {
            const b = await apiClient.get<Budget>("/assistant-trial/budget");
            if (active && selectedId.current===id) setConfig(c => c ? { ...c, budget: b.data } : c);
          } catch { if (active) setPollNotice("利用料金を取得できませんでした。再確認しています。"); }
        }
      } catch { if (active) setPollNotice("進捗を取得できませんでした。再確認しています。作業は再送していません。"); }
      finally { polling = false; }
    }, 2500);
    return () => { active = false; clearInterval(timer); };
  }, [selected?.id, selected?.status, adminView]);

  useEffect(()=>{setSource(null);setSourceChecked(false);setConfirmRollback(false);},[selected?.id]);
  useEffect(()=>{setReply("");},[selected?.id,selected?.pending_question?.question_id]);
  useEffect(()=>{setReviewMessage("");},[selected?.task_review?.id]);

  function showError(e: any) { const detail=e?.response?.data?.detail;const text=typeof detail==="string" ? detail : Array.isArray(detail) ? detail.map(item=>item.msg || item.message).filter(Boolean).join("。") : detail?.message || e?.message || "処理を進められませんでした。入力内容を確認してください。";setError(!adminView && /(?:API|revision|draft_id|If-Match|接続先|予算|token|timeout)/i.test(text) ? "処理状況をシステム側で確認しています。回答した内容はこの画面に保持されています。" : text); }
  async function act(fn: () => Promise<void>) {
    pendingRequests.current++;setError(""); setBusy(true);
    try { await fn(); } catch (e) { showError(e); } finally { pendingRequests.current--;setBusy(pendingRequests.current>0); }
  }
  async function submitDecision(url:string,data:{review_id:string;approve:boolean;message:string},faxId?:string) {
    if(!selected)return;
    setPendingDecision({runId:selected.id,reviewId:data.review_id,faxId});
    try {await assistantApi.post(url,data);setPendingDecision(null);}
    catch(e:any) {
      if(!e.response || e.response.status>=500)return;
      setPendingDecision(null);throw e;
    }
  }
  async function submitReply(data:{message:string;question_id?:string;option?:string}) {
    if(!selected)return;
    const id=selected.id;
    setPendingReply({runId:id,questionId:data.question_id});
    try {await assistantApi.post(`/assistant-trial/runs/${id}/reply`,data);setPendingReply(null);}
    catch(e:any) {
      if(!e.response || e.response.status>=500)return;
      setPendingReply(null);throw e;
    }
  }
  async function send(e: FormEvent) {
    e.preventDefault();
    await act(async () => {
      const key = requestKey || crypto.randomUUID(); setRequestKey(key);
      setPendingCreate(key);
      try {
        const response = await assistantApi.post<Run>("/assistant-trial/runs", { request_key: key, task_id: taskId, ...(taskId === "hospital_pending_faxes" ? {} : {selection_id:selection?.selection_id,target_keys:targetKeys,layout_id:layoutId ? Number(layoutId):null}) });
        if(selectedId.current!==response.data.id){selectedId.current=response.data.id;setSelected(response.data);}
        setPendingCreate(null);setRequestKey("");await refreshAfterCommand(response.data.id);
      } catch(e:any) {
        if(!e.response || e.response.status>=500){
          try {await reconcileCreate(key);}catch {setPollNotice("受付状況を再確認しています。同じ依頼は再送していません。");}
          return;
        }
        setPendingCreate(null);throw e;
      }
    });
  }
  async function control(action: string) {
    if (!selected || controlling) return;
    setConfirmRollback(false);
    const id = selected.id;
    setControlling(true);
    setControlNotice(action==="stop" ? "接続先へ停止状況を確認しています…" : action==="rollback" ? "開始前の状態へ戻しています…" : "処理結果を確認しています…");
    try {await act(async () => {
      try {
        const response=await assistantApi.post<Run | {run:Run}>(`/assistant-trial/runs/${id}/${action}`);
        if(selectedId.current!==id)return;
        const run="run" in response.data ? response.data.run : response.data;
        receiveRun(run);
        setControlNotice(["stop_pending","rollback_stop_pending"].includes(run.status)
          ? "停止を再確認しました。業務の書込みは停止済みですが、接続先の停止完了はまだ確認できていません。履歴を残して作業一覧に戻れます。"
          : run.status==="stopped" ? "停止の確認が完了しました。"
          : run.status==="rolled_back" ? "開始前の状態への復元と停止の確認が完了しました。"
          : "処理結果を取得しました。現在の状態を確認してください。");
      } catch(e:any) {
        if(selectedId.current!==id)return;
        setControlNotice(!e.response || e.response.status>=500
          ? "操作結果を受信できませんでした。停止完了とは判断せず、現在の状態を再確認しています。"
          : "操作を完了できませんでした。下のエラー内容を確認してください。");
        if(e.response && e.response.status<500)throw e;
      }
      await refreshAfterCommand(id);
    });}
    finally {setControlling(false);}
  }
  const task = tasks.find(t => t.id === taskId && t.domain === domain);
  const targetsReady=taskId==="hospital_pending_faxes" ? !!selection : !!selection && (!task?.month || !!selection.inputs?.month) && !!targetKeys.length && (task?.target_kind!=="menu" || !!task?.date || !!layoutId);
  const ready = config && task && targetsReady && (!config.missing.length || task.requires_agent===false) && config.domains[domain]?.baseline_ready;
  function resetTargets() {targetRequest.current++;setLoadingTargets(false);setSelection(null);setTargetKeys([]);setLayoutId("");setRequestKey("");}
  async function fetchTargets(facility=schoolId,requestedDay?:string) {
    resetTargets();const request=targetRequest.current;
    setLoadingTargets(true);
    const key=crypto.randomUUID();
    const body={request_key:key,inputs:{...(task?.month && targetMonth ? {month:targetMonth}:{}),...(requestedDay ? {service_date:requestedDay}:{}),...(facility && task?.domain==="school-lunch" ? {facility_id:Number(facility)}:{}),...(taskId==="shift_absence" && absenceDate ? {absence_date:absenceDate}:{})}};
    let accepted=false;
    try {
      while(request===targetRequest.current){
        try {
          const response=accepted ? await apiClient.get(`/assistant-trial/target-lookups/${key}`) : await assistantApi.post(`/assistant-trial/tasks/${taskId}/target-lookups`,body);
          if(request!==targetRequest.current)return;
          accepted=true;
          if(response.data.state==='failed')throw {response:{status:409,data:{detail:response.data.result.message}}};
          if(response.data.state==='completed'){
            const result=response.data.result as Selection;
            setSelection(result);setTargetKeys(requestedDay || task?.target_kind==="month" ? result.choices.map(item=>item.key) : task?.target_kind==="school" && facility ? [facility] : []);setLayoutId("");setRequestKey("");return;
          }
        } catch(e:any){
          if(e.response && e.response.status<500)throw e;
          // Discovery is read-only and keyed. Reconnect to the same lookup.
        }
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
    } catch(e) {if(request===targetRequest.current)showError(e);} finally {if(request===targetRequest.current)setLoadingTargets(false);}
  }
  function chooseWorkDay(day:string){
    setServiceDate(day);const matches=selection!.choices.filter(item=>item.data.service_date===day);setRequestKey("");
    if(matches.length)setTargetKeys(matches.map(item=>item.key));else void fetchTargets(schoolId,day);
  }
  useEffect(()=>()=>{targetRequest.current++;},[]);
  async function taskDecision(approve:boolean) {
    if (!selected?.task_review) return;
    await act(async()=>{await submitDecision(`/assistant-trial/runs/${selected.id}/decision`,{review_id:selected.task_review!.id,approve,message:reviewMessage});setReviewMessage("");await refreshAfterCommand(selected.id);});
  }
  async function download(file: { id: string; name: string }) {
    if (!selected) return;
    const response = await apiClient.get(`/assistant-trial/runs/${selected.id}/artifacts/${file.id}`, { responseType: "blob",timeout:BUSINESS_TIMEOUT });
    const url = URL.createObjectURL(response.data); const link = document.createElement("a"); link.href = url; link.download = file.name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  async function decide(fax: Fax, approve: boolean) {
    if (!selected || !fax.review) return;
    await act(async () => { await submitDecision(`/assistant-trial/runs/${selected.id}/faxes/${fax.fax_id}/decision`, { review_id: fax.review!.id, approve, message: corrections[fax.fax_id] || "" },fax.fax_id); await refreshAfterCommand(selected.id); });
  }
  const view=selected ? businessProgress(selected) : null;
  const terminal=!!selected && (adminView ? ["completed","stopped","rolled_back","failed"].includes(selected.status) : ["completed","archived"].includes(view!.state));
  const awaitingSource=selected?.status==="waiting_input" && (adminView || view?.next_action==="source") && selected.task_id==="lunch_import" && !(selected.source_attached ?? selected.events?.some(event=>event.kind==="source"));
  const awaitingReview=selected?.status==="running" && (adminView || view?.next_action==="approve") && selected.task_review?.state==="awaiting_approval";
  const faxReviews=selected?.fax_items?.filter(fax=>fax.state==="awaiting_approval") || [];
  const hasFaxReview=(selected?.fax_page?.summary.states.awaiting_approval ?? faxReviews.length)>0;
  const question=selected?.status==="waiting_input" ? selected.pending_question : undefined;
  const decisionPending=!!selected && pendingDecision?.runId===selected.id;
  const replyPending=!!selected && pendingReply?.runId===selected.id;
  const needsAnswer=selected?.status==="waiting_input" && !!selected.pending_question && !awaitingSource && (adminView || view?.next_action==="answer");
  const stage=selected ? selected.status==="completed" ? 3 : selected.task_review || selected.fax_items?.some(fax=>!!fax.review) || needsAnswer || awaitingSource ? 2 : 1 : choosingTask ? 0 : 1;
  const attention=!!selected && ["failed","reconciliation_required","budget_reached","stop_pending","rollback_stop_pending"].includes(selected.status);
  const attentionReason=attention ? [...(selected?.conversation || [])].reverse().find(message=>message.role==="assistant")?.text : "";
  const latestAgentText=[...(selected?.conversation || [])].reverse().find(message=>message.role==="assistant")?.text;
  const technicalTitle=selected ? selected.status==="completed" ? "作業が完了しました" : selected.status==="rolled_back" ? "開始前の状態に戻しました" : selected.status==="stopped" ? "作業を停止しました" : selected.status==="stopping" ? "作業の停止を確認しています" : awaitingSource ? "原本ファイルを添付してください" : awaitingReview || hasFaxReview ? "内容を確認してください" : needsAnswer ? "この質問にお答えください" : attention ? "処理状況の確認が必要です" : selected.task_review?.state==="approved" ? "承認した内容で作業を進めています" : selected.task_review?.state==="needs_correction" ? "訂正内容を反映しています" : "エージェントが作業を進めています" : choosingTask ? "どの作業を行いますか？" : "こちらの作業はいかがですか？";
  const nextTitle=!adminView && view ? view.title : technicalTitle;
  const messages=adminView ? selected?.conversation : selected?.business_messages;
  function selectTask(id:string) {const value=tasks.find(t=>t.id===id)!;setDomain(value.domain);setTaskId(id);setTargetMonth("");setServiceDate("");setSchoolId("");setAbsenceDate("");resetTargets();setChoosingTask(false);setError("");}
  function newTask(){selectedId.current=undefined;setSelected(null);setChoosingTask(true);resetTargets();setError("");setControlNotice("");}
  async function openRun(id:string){selectedId.current=id;setControlNotice("");await act(()=>load(id));}
  useEffect(()=>{
    if(adminView || selected || choosingTask || !task || !config?.domains[domain]?.baseline_ready)return;
    const timer=setTimeout(()=>{void fetchTargets();},250);
    return ()=>clearTimeout(timer);
  },[task?.id,domain,targetMonth,absenceDate,schoolId,choosingTask,selected?.id,config?.domains[domain]?.baseline_ready,adminView]);
  const focusStep=error ? "error:"+error : selected ? [selected.id,selected.status,selected.pending_question?.question_id,selected.task_review?.id,selected.task_review?.state].join(":") : choosingTask ? "choose" : "target:"+taskId;
  useEffect(()=>{if(adminView)return;const element=pageRef.current?.querySelector<HTMLElement>(error ? ".assistant-error" : selected ? needsAnswer ? ".answer-panel h3" : ".next-action h3" : ".request-panel h2");if(element){element.tabIndex=-1;element.focus({preventScroll:true});element.scrollIntoView?.({block:"start"});}},[focusStep,adminView]);
  function resourcePages(kind:'fax'|'artifact') {
    if(!selected)return null;
    const page=kind==='fax'?selected.fax_page:selected.artifact_page;
    if(!page)return null;
    const back=kind==='fax'?faxBack:artifactBack;
    return <nav aria-label={kind==='fax'?'FAXのページ':'ファイルのページ'} style={{display:'flex',flexWrap:'wrap',alignItems:'center',gap:8}}>
      {kind==='fax'&&selected.fax_page&&<>
        <span>全{selected.fax_page.summary.total}件・確認待ち{selected.fax_page.summary.states.awaiting_approval??0}件</span>
        <label>表示するFAX<select value={selected.fax_page.state} disabled={busy||decisionPending}
          onChange={event=>void act(()=>moveResources('fax','reset',event.target.value))}>
          <option value="">すべて</option><option value="awaiting_approval">確認待ち</option>
        </select></label>
      </>}
      <button title="前のページ" aria-label="前のページ" disabled={busy||decisionPending||!back.length}
        onClick={()=>void act(()=>moveResources(kind,'previous'))}>&larr;</button>
      <button title="次のページ" aria-label="次のページ" disabled={busy||decisionPending||page.next_after===null}
        onClick={()=>void act(()=>moveResources(kind,'next'))}>&rarr;</button>
      <button disabled={busy||decisionPending} onClick={()=>void act(()=>moveResources(kind,'reset'))}>最初のページ</button>
    </nav>;
  }
  function renderArtifacts(){
    const run=selected;
    if(!run || !run.artifacts?.length && !artifactBack.length)return null;
    return <section className="artifact-panel">
      <h3>{run.status==="completed" ? "完成したファイル" : "この依頼のファイル"}</h3>
      {resourcePages('artifact')}
      {run.artifacts.map(file=><div className="artifact-row" key={file.id}>
        <span><strong>{file.name}</strong><small>{["rolled_back","rollback_stop_pending"].includes(run.status)
          ? "元に戻したため取得できません" : `${Math.max(1,Math.ceil(file.size/1024))} KB`}</small></span>
        <button disabled={busy || ["rolled_back","rollback_stop_pending"].includes(run.status)} onClick={()=>act(()=>download(file))}>ダウンロード</button>
      </div>)}
    </section>;
  }
  return <main className="assistant-page" ref={pageRef}>
    <header className="assistant-heading"><div><p className="eyebrow">業務アシスタント</p><h1>{adminView ? "利用料金の管理" : "AIに依頼"} <span className="trial-tag">試用版</span></h1><p className="assistant-description">{adminView ? "利用金額と上限を確認できます。" : "作業を選んだら、あとは質問に答えるだけ。内容を確認してから保存します。"}</p></div><Link className="quiet-link" href={adminView ? "/assistant" : "/assistant-admin"}>{adminView ? "作業画面へ" : "利用料金・管理"}</Link></header>
    {error && <div role="alert" className="assistant-error">{error}</div>}
    {pollNotice && <p role="status" className="muted">{pollNotice}</p>}
    {pendingCreate && <p className="assistant-notice">依頼の受付を確認しています。同じ依頼は再送していません。</p>}
    {replyPending && <p className="assistant-notice">回答の反映を確認しています。同じ回答は再送していません。</p>}
      {decisionPending && <p role="status" className="assistant-notice">承認・訂正の反映を確認しています。同じ操作は再送していません。</p>}
    {adminView && config?.providers && <section className="assistant-panel"><h2>エージェントの接続先</h2><p>変更は新しい依頼から適用します。実行中・確認待ちの依頼は元の接続先で続けます。</p><div className="answer-options">{config.providers.map(provider=><button key={provider.id} disabled={busy} aria-pressed={config.provider_id===provider.id} onClick={()=>act(async()=>{await apiClient.put("/assistant-trial/provider",{provider:provider.id});await load(selected?.id);})}>{provider.label}{config.provider_id===provider.id?"（使用中）":""}</button>)}</div>{config.providers.map(provider=><p key={provider.id}>{provider.label}：{provider.missing.length?"未設定："+provider.missing.join("、"):"設定済み"}</p>)}<p className="muted">Geminiの料金は報告トークン数と管理者が設定した単価による概算です。思考トークンを含み、割引・無料枠は適用しません。Claudeは接続先の定価換算値です。請求確定額とは異なります。</p></section>}
    {adminView && config && <section className="assistant-panel"><h2>今月の利用料金</h2><dl className="costs"><div><dt>使用額（概算・定価換算）</dt><dd>{dollars(config.budget.spent_cents)}</dd></div><div><dt>実行中の予約額</dt><dd>{dollars(config.budget.reserved_cents)}</dd></div><div><dt>月次上限</dt><dd>{dollars(config.budget.monthly_cents)}</dd></div><div><dt>利用可能額</dt><dd>{dollars(config.budget.available_cents)}</dd></div></dl><p className="muted">{config.budget.notice} 米ドル表示です。既存OCR等の料金は含みません。</p><form onSubmit={e=>{e.preventDefault();act(async()=>{await apiClient.put("/assistant-trial/budget",{monthly_cents:cents(month)});await load(selected?.id);});}}><label>月次予算（USD）<input value={month} onChange={e=>setMonth(e.target.value)} inputMode="decimal" placeholder={(config.budget.monthly_cents/100).toFixed(2)} required/></label><button className="primary" disabled={busy}>上限を保存する</button></form></section>}
    {!adminView && config && (config.missing.length>0 && (!task || task.requires_agent!==false) || (selected ? selected.domains.some(key=>!config.domains[key]?.baseline_ready) : choosingTask ? !Object.values(config.domains).some(value=>value.baseline_ready) : !config.domains[domain]?.baseline_ready)) && <div className="assistant-notice"><strong>試用の準備中です</strong><p>管理者の接続・試用データ設定が完了すると、選んだ作業を開始できます。</p><details><summary>設定状況</summary><p>{config.missing.length ? "接続先が未設定です。" : "接続先は設定済みです。"}</p>{Object.entries(config.domains).map(([key,value])=><p key={key}>{labels[key]}：{value.baseline_ready ? value.data==="empty_schema_no_real_order_test" ? "業務データの準備が必要" : "準備済み" : "未設定"}</p>)}</details></div>}
    {!adminView && <ol className="assistant-steps" aria-label="作業の進み具合">{["作業を選ぶ","対象を選ぶ・処理","質問・内容の確認","完了"].map((label,i)=><li key={label} aria-current={stage===i ? "step" : undefined} className={i<stage ? "done" : ""}><span>{i<stage ? "✓" : i+1}</span>{label}</li>)}</ol>}
    {!adminView && !selected && runs.some(run=>["source","question","review","working","checking"].includes(businessProgress(run).state) && !!run.business) && <section className="assistant-panel"><h2>続きがある作業</h2><p className="muted">作業を開くと、現在の進み具合や次に回答する質問を確認できます。</p>{runs.filter(run=>["source","question","review","working","checking"].includes(businessProgress(run).state) && !!run.business).map(run=><button className="history-row" key={run.id} onClick={()=>openRun(run.id)}><strong>{run.instruction}</strong><span className="status-tag">{businessLabels[businessProgress(run).state]} →</span></button>)}</section>}
    {!adminView && !selected && <section className="assistant-panel request-panel"><div className="panel-heading"><div><p className="eyebrow">{choosingTask ? "作業の選択" : labels[domain]}</p><h2>{nextTitle}</h2></div>{!choosingTask && <button className="quiet" onClick={()=>{setChoosingTask(true);resetTargets();}}>作業を選び直す</button>}</div>
      {choosingTask ? <><div className="task-domain-grid">{Object.entries(labels).map(([id,label])=><section className="task-domain-group" key={id} aria-label={label}><h3>{label}</h3><div className="assistant-task-list">{tasks.filter(t=>t.domain===id).sort((a,b)=>Number(!!b.date)-Number(!!a.date)).map((t,i,list)=><Fragment key={t.id}>{id==="school-lunch" && (i===0 || !!list[i-1].date!==!!t.date) && <h4 className="task-group-label">{t.date ? "日ごとの作業" : "月ごとの作業"}</h4>}<button className="assistant-task" onClick={()=>selectTask(t.id)}><span><strong>{t.label}</strong><small>{t.description}</small></span><span aria-hidden="true">→</span></button></Fragment>)}</div></section>)}</div>{!tasks.length && <p role="status">作業項目を読み込んでいます…</p>}</> : <form onSubmit={send}>
        <div className="context-strip"><strong>{task?.label}</strong><p>{task?.description}</p></div>
        <div className="assistant-fields">
          {taskId==="shift_absence" && <section className="work-day-proposals"><h3>いつの欠勤ですか？</h3><div className="answer-options">{selection?.absence_days?.map(day=><button type="button" key={day.date} aria-pressed={(absenceDate || selection.inputs?.absence_date)===day.date} onClick={()=>{setAbsenceDate(day.date);resetTargets();}}>{day.label}</button>)}</div><label>別の日を指定する<input type="date" value={absenceDate || selection?.inputs?.absence_date || ""} onChange={e=>{setAbsenceDate(e.target.value);resetTargets();}}/></label><p className="muted">勤務期間は欠勤日から自動で確認します。</p></section>}
          {task?.domain==="school-lunch" && !!selection?.schools.length && <fieldset className="target-choices school-choices"><legend>どの学校の作業ですか？</legend>{selection.schools.map(x=><label className="target-choice" key={x.id}><input type="radio" name="school" value={x.id} checked={schoolId===String(x.id)} onChange={()=>{setSchoolId(String(x.id));setServiceDate("");setTargetMonth("");resetTargets();}}/><span>{x.name}</span></label>)}</fieldset>}
        </div>
        {task?.month && taskId!=="shift_absence" && (task.domain!=="school-lunch" || schoolId) && <section className="work-day-proposals"><h3>{taskId==="shift_next_month" ? "翌月の勤務案を作成しますか？" : "この月の作業はいかがですか？"}</h3><div className="answer-options">{selection?.month_choices?.map((period,index)=><button type="button" key={period.month} aria-pressed={(targetMonth || selection.inputs?.month)===period.month} onClick={()=>{setTargetMonth(period.month);resetTargets();}}><strong>{index===0 ? "おすすめ：" : ""}{period.label}</strong><small>{period.reason}</small></button>)}</div>{selection && !selection.month_choices?.length && <p>登録済みの対象月がありません。別の月を指定して確認することもできます。</p>}<label>別の月を指定する<input type="month" value={targetMonth || selection?.inputs?.month || ""} onChange={e=>{setTargetMonth(e.target.value);resetTargets();}}/></label></section>}
        {!!selection?.batches?.length && <section className="work-day-proposals"><h3>こちらの対象で進めますか？</h3><div className="answer-options">{selection.batches.map((batch,index)=><button type="button" key={index} aria-pressed={batch.keys.length===targetKeys.length && batch.keys.every(key=>targetKeys.includes(key))} onClick={()=>{setTargetKeys(batch.keys);setRequestKey("");}}><strong>{batch.label}</strong><small>{batch.reason}</small></button>)}</div><p className="muted">下の一覧から個別に選ぶこともできます。</p></section>}
        {loadingTargets && <p role="status" className="muted">登録済みの対象を確認しています…</p>}
        {taskId==="hospital_pending_faxes" && <div className="assistant-notice">登録済みの未処理FAX {selection?.choices.length ?? "確認中"}件をまとめて処理します。開始時にもう一度対象を取得し、処理できたものから質問・内容確認へ進みます。{!!selection?.choices.length && <ul>{selection.choices.map(item=><li key={item.key}>{item.label}</li>)}</ul>}</div>}
        {task?.date && schoolId && <section className="work-day-proposals"><h3>こちらの作業日はいかがですか？</h3>{loadingTargets ? <p role="status">登録された献立と帳票の保存状況から、作業日を確認しています。そのままお待ちください。</p> : selection && !selection.work_days?.length ? <p>この学校には登録済みの献立がありません。月次の「献立・調理手配書を取り込む」から原本を添付してください。</p> : <div className="answer-options">{selection?.work_days?.map((day,index)=><button type="button" key={day.service_date} aria-pressed={serviceDate===day.service_date} onClick={()=>chooseWorkDay(day.service_date)}><strong>{index===0 ? "おすすめ：" : ""}{new Date(day.service_date+"T12:00:00+09:00").toLocaleDateString("ja-JP",{month:"long",day:"numeric",weekday:"short",timeZone:"Asia/Tokyo"})}</strong><span>{day.menu_names.join("・")}</span><small>{day.reason}</small></button>)}</div>}</section>}
        {task?.date && schoolId && <label className="other-work-day">別の日を指定する<input type="date" value={serviceDate} disabled={loadingTargets} onChange={e=>{const day=e.target.value;setServiceDate(day);if(day)void fetchTargets(schoolId,day);else setTargetKeys([]);}}/></label>}
        {task?.date && serviceDate && selection && schoolId && <div className="assistant-notice">{targetKeys.length ? <><strong>この日の献立をまとめて処理します</strong><p>{selection.choices.filter(x=>targetKeys.includes(x.key)).map(x=>x.label).join(" ／ ")}</p><p>作業工程表・動線図・実施価格を一度に確認できます。</p></> : "この日の献立は未登録です。月次の「献立・調理手配書を取り込む」を先に行ってください。"}</div>}
        {selection && !task?.date && task?.target_kind!=="month" && task?.target_kind!=="school" && taskId!=="hospital_pending_faxes" && <fieldset className="target-choices"><legend>{task?.target_kind==="school" ? "学校を選んでください" : taskId==="shift_absence" ? "欠勤する職員を選んでください" : "作業する対象を選んでください"}</legend>{!selection.choices.length && <p className="muted">{task?.target_kind==="menu" && !schoolId ? "学校を選ぶと、登録済みの献立が表示されます。" : "対象がありません。登録内容・対象月を確認してください。"}</p>}{selection.choices.map(choice=><label className="target-choice" key={choice.key}><input type={task?.target_kind==="staff" ? "radio" : "checkbox"} name="target" checked={targetKeys.includes(choice.key)} onChange={e=>{setTargetKeys(keys=>task?.target_kind==="staff" ? [choice.key] : e.target.checked ? [...keys,choice.key] : keys.filter(k=>k!==choice.key));setRequestKey("");}}/><span>{choice.label}</span></label>)}</fieldset>}
        {task?.target_kind==="menu" && !task.date && !!selection?.choices.length && <fieldset className="target-choices"><legend>使う図面を選んでください</legend>{selection.layouts.map(x=><label className="target-choice" key={x.id}><input type="radio" name="layout" value={x.id} checked={layoutId===String(x.id)} onChange={()=>setLayoutId(String(x.id))}/><span>{x.name}（版 {x.version}）</span></label>)}</fieldset>}
        <div className="assistant-footer"><div><p className="muted">{ready ? "開始後は、画面に届く質問への回答と内容の確認だけで進みます。" : loadingTargets ? "対象を確認しています。" : config?.missing.length && task?.requires_agent!==false ? "管理者の接続設定を待っています。" : task?.domain==="school-lunch" && !schoolId ? "学校を選んでください。対象月・作業日は登録内容から提案します。" : taskId==="shift_absence" && !targetKeys.length ? "欠勤する職員を選んでください。" : "提案または一覧から対象を選んでください。"}</p></div><button className="primary" disabled={busy || !!pendingCreate || !ready || loadingTargets}>この内容で始める</button></div>
        {task && <button type="button" className="quiet retry-targets" disabled={loadingTargets || busy} onClick={()=>fetchTargets()}>対象を再読み込み</button>}
      </form>}
    </section>}
    {selected && <section className="assistant-panel active-work" aria-label="進行中の作業"><div className="panel-heading"><div><p className="eyebrow">{selected.domains.map(id=>labels[id]).join(" ／ ")}</p><h2>{selected.instruction}</h2></div><span className={selected.status==="completed" ? "status-tag complete" : terminal ? "status-tag neutral" : "status-tag"}>{adminView ? awaitingReview || hasFaxReview ? "内容の確認待ち" : states[selected.status] || selected.status : businessLabels[view!.state]}</span></div>
      {!!selected.task_inputs?.targets?.length && <p className="context-strip">対象：{selected.task_inputs.targets.map(x=>x.label).join(" ／ ")}</p>}
      <div className={terminal ? "next-action finished" : "next-action"} role="status"><span className="next-action-marker" aria-hidden="true">{selected.status==="completed" ? "✓" : needsAnswer || awaitingReview || awaitingSource || hasFaxReview ? "?" : "…"}</span><div><h3>{nextTitle}</h3><p>{!adminView ? view!.message : selected.status==="completed" ? selected.artifacts?.length ? "保存・出力が終わりました。下の完成ファイルを確認できます。" : "確認が完了しました。内容は下の確認結果と履歴で見ることができます。" : selected.status==="rolled_back" ? "この依頼で行った変更は戻しました。実行履歴は残っています。" : selected.status==="stopped" ? "これ以上の処理は行いません。変更も戻したい場合は「開始前に戻す」を選んでください。" : selected.status==="stopping" ? "追加の業務書込みを停止しました。接続先の停止と利用料金を確認しています。" : awaitingSource ? "選択した学校・対象月の原本を添付してください。" : awaitingReview || hasFaxReview ? "結果を確認し、そのまま進めるか、訂正したい箇所を教えてください。" : needsAnswer ? "回答すると、続きの作業を自動で進めます。" : selected.status==="budget_reached" ? "作業は未完了です。管理画面で利用上限を確認してください。" : ["stop_pending","rollback_stop_pending"].includes(selected.status) ? "業務の書込みは停止済みです。「停止を再確認する」で、接続先の停止を確認できます。" : attention ? "処理は完了していません。下の理由を確認してください。" : "この画面でお待ちください。確認が必要になったら質問が表示されます。"}</p>{adminView && attentionReason && <p className="attention-reason">{attentionReason}</p>}</div></div>
      {terminal && renderArtifacts()}
      {adminView && <p>使用額 {dollars(selected.spent)}</p>}
      {needsAnswer && <section className="answer-panel" aria-label="エージェントからの質問"><p className="process-question-progress" hidden={!question?.process_context}>{question?.process_context?.kind?.startsWith("execution.") ? "時刻・担当者・作業区分の確認" : "調理方法・作業場所の確認"}：{question?.process_context?.answered} / {question?.process_context?.total}{question?.process_context?.kind?.startsWith("execution.") ? "項目" : "工程"}</p><h3>{question?.text || latestAgentText || "確認事項への回答をお願いします"}</h3>{question?.question_map ? <DrawingQuestion key={selected.id+question.question_id} runId={selected.id} questionId={question.question_id!} map={question.question_map} busy={busy || replyPending} onAnswer={option=>act(async()=>{await submitReply({message:option,question_id:question.question_id,option});await refreshAfterCommand(selected.id);})}/> : <><div className="answer-options">{question?.options?.map(option=><button disabled={busy || replyPending} key={option} onClick={()=>act(async()=>{await submitReply({message:option,question_id:question.question_id,option});await refreshAfterCommand(selected.id);})}>{option}</button>)}</div>{question?.process_context?.kind!=="grouping" && <form onSubmit={e=>{e.preventDefault();act(async()=>{await submitReply({message:reply,question_id:question?.question_id});setReply("");await refreshAfterCommand(selected.id);});}}><label>{question?.options?.length ? "文章でも回答できます" : "回答"}<textarea value={reply} onChange={e=>setReply(e.target.value)} placeholder="ここに回答を入力してください" required/></label><button className="primary" disabled={busy || replyPending || !reply.trim()}>回答して続ける</button></form>}</>}</section>}
      {awaitingSource && <form className="answer-panel" onSubmit={e=>{e.preventDefault();act(async()=>{if(!source || !sourceChecked)throw new Error("原本の学校・対象月を確認してください");const form=new FormData();form.append("file",source);form.append("domain","school-lunch");form.append("binding",JSON.stringify({facility_id:String(selected.task_inputs!.inputs!.facility_id),month:selected.task_inputs!.inputs!.month}));await assistantApi.post(`/assistant-trial/runs/${selected.id}/sources`,form);await submitReply({message:"選択した学校・対象月と原本を照合しました。",question_id:selected.pending_question?.question_id});setSource(null);setSourceChecked(false);await refreshAfterCommand(selected.id);});}}><label>献立・調理手配書の原本<input type="file" accept=".pdf,.xls,.xlsx" required onChange={e=>{setSource(e.target.files?.[0] || null);setSourceChecked(false);}}/></label><p>学校：{selected.task_inputs?.targets?.[0]?.label} ／ 対象月：{selected.task_inputs?.inputs?.month}</p><label className="check-line"><input type="checkbox" checked={sourceChecked} required onChange={e=>setSourceChecked(e.target.checked)}/>原本の学校・対象月が、表示内容と一致しています</label><button className="primary" disabled={busy || !source || !sourceChecked}>原本を添付して続ける</button></form>}
      {selected.task_review && <div className="result-archive"><section className="review-panel" key={selected.task_review.id} aria-label="結果の確認と承認"><h3>{awaitingReview ? "作成した内容" : "確認した内容"}</h3><p>{selected.task_review.summary}</p>{selected.lunch_review && <section className="evidence"><h4>食材ごとの当日の実績</h4><ResultView value={selected.lunch_review} expanded/></section>}{selected.shift_review ? <ShiftReview value={selected.shift_review}/> : selected.task_review.evidence.filter(entry=>!selected.lunch_review || entry.operation!=="lunch_get_process").map((entry,i)=><section className="evidence" key={i}><h4>{({shift_get_run:"勤務表",shift_get_validation:"人員不足・勤務制約",shift_get_constraints:"希望休・勤務条件",shift_get_staff:"職員",shift_get_demands:"必要人数",shift_get_workdays:"勤務日",shift_get_settings:"勤務設定",shift_get_period:"勤務期間",lunch_get_menus:"献立",lunch_get_process:"調理工程",lunch_get_schedules:"工程表",lunch_get_routes:"動線図",lunch_get_procurement:"必要量・在庫・発注案",lunch_get_price_source:"実施価格の計算入力",lunch_get_price_result:"実施価格",lunch_get_price_quantities:"原本の1人量",lunch_get_source_document:"原本の抽出結果",lunch_get_source_review:"原本の照合結果",hospital_get_recognition_review:"原本セルと認識値",hospital_get_fax:"FAXの状態",hospital_get_workflow:"確定注文の状態"} as Record<string,string>)[entry.operation] || "業務情報"}</h4><ResultView value={entry.data} expanded/></section>)}
        {!selected.shift_review && !!selected.task_review.actions.length && <section className="evidence"><h4>承認後に保存する内容</h4>{selected.task_review.actions.map((action,i)=><ResultView key={i} value={action.parameters.body} expanded/>)}</section>}
        {awaitingReview && <div className="review-decisions"><section className="approval-choice"><h4>この内容でよければ</h4><p className="muted">確認した内容で保存・出力へ進みます。</p><button className="primary" disabled={busy || decisionPending} onClick={()=>taskDecision(true)}>この内容で進める</button></section><form className="correction-panel" onSubmit={e=>{e.preventDefault();taskDecision(false);}}><label>訂正してほしい箇所<textarea value={reviewMessage} onChange={e=>setReviewMessage(e.target.value)} placeholder="訂正が必要なときだけ入力してください" required/></label><button disabled={busy || decisionPending || !reviewMessage.trim()}>訂正を依頼する</button></form></div>}
        {selected.shift_review && awaitingReview && <p className="assistant-notice">承認すると、この勤務表を確定してExcelを出力します。</p>}
        {!awaitingReview && <p className="muted">{({awaiting_approval:"処理状態の確認が必要です",approved:"承認済み",needs_correction:"訂正を依頼しました",stale:"元データが変わったため、再確認が必要です"} as Record<string,string>)[selected.task_review.state]}</p>}
        {selected.task_review.decision?.message && <p>{selected.task_review.decision.message}</p>}
      </section></div>}
      {(selected.fax_page?.summary.total??0)>0 && resourcePages('fax')}
      {!!selected.fax_items?.length && <section className="fax-list"><h3>FAXの処理状況 <small>{(selected.fax_page?.summary.states.completed??selected.fax_items.filter(fax=>fax.state==="completed").length)} / {(selected.fax_page?.summary.total??selected.fax_items.length)}件完了</small></h3>{[...selected.fax_items].sort((a,b)=>Number(b.state==="awaiting_approval")-Number(a.state==="awaiting_approval")).map(fax=><article key={fax.fax_id} className="fax-item"><h4>{fax.snapshot.name}</h4><p>{fax.snapshot.facility} ／ {fax.snapshot.week} ／ {({pending:"処理待ち",processing:"読み取り中",ready:"内容を照合中",awaiting_approval:"内容を確認してください",approved:"確定処理中",needs_correction:"訂正中",completed:"注文確定済み",blocked:"要確認"} as Record<string,string>)[fax.state] || fax.state}</p>{fax.review && <><p>{fax.review.summary}</p><button disabled={busy} onClick={()=>act(()=>download(fax.review!.original))}>原本PDFを開く</button><section><h4>読み取った数量</h4><div className="sheet"><table><thead><tr>{(fax.review.sheet.header || fax.review.sheet.fields || []).map((cell,i)=><th key={i}>{String(cell ?? "")}</th>)}</tr></thead><tbody>{fax.review.sheet.rows?.map((row,i)=><tr key={i}>{row.map((cell,j)=><td key={j}>{String(cell ?? "")}</td>)}</tr>)}</tbody></table></div></section>{fax.state==="awaiting_approval" && selected.status==="running" && <><button className="primary" disabled={busy || decisionPending} onClick={()=>decide(fax,true)}>この内容で確定へ進む</button><section className="correction-panel"><h4>訂正してほしい箇所がある</h4><label>訂正内容<textarea value={corrections[fax.fax_id] || ""} onChange={e=>setCorrections(c=>({...c,[fax.fax_id]:e.target.value}))}/></label><button disabled={busy || decisionPending || !corrections[fax.fax_id]?.trim()} onClick={()=>decide(fax,false)}>訂正を依頼する</button></section></>}</>}{fax.decision?.message && <p>{fax.decision.message}</p>}</article>)}</section>}
      {!terminal && renderArtifacts()}
      {!adminView && <div className="assistant-footer"><span className="muted">回答した内容は保存されています。作業一覧へ戻っても、この作業は引き続き確認できます。</span><button className="primary" onClick={newTask}>{terminal || selected.status==="rollback_stop_pending" ? "別の作業を始める" : "作業一覧に戻る"}</button></div>}
      <details className="conversation-history"><summary>これまでのやり取り{messages?.length ? `（${messages.length}件）` : ""}</summary><div role="log">{messages?.map(message=><div className={message.role==="user" ? "chat-bubble user-message" : "chat-bubble"} key={message.id}><strong>{message.role==="user" ? "あなた" : "エージェント"}</strong><p>{message.text}</p></div>)}</div></details>
      {adminView && selected.status==="reconciliation_required" && <button className="primary" disabled={controlling || busy} onClick={()=>control("resume")}>保存済みの結果を確認して再開</button>}
      {adminView && selected.business?.state==="checking" && <section className="assistant-notice"><h3>管理者による確認が必要な業務</h3>{selected.events?.filter(event=>event.kind==="business_support").slice(-1).map(event=><p key={event.id}>{event.payload.message}</p>)}<button disabled={busy || controlling} onClick={()=>control("reconcile-business")}>業務結果を再照合する</button><p>結果未確定の更新は再送せず、保存済みの証跡を確認します。</p></section>}
      {adminView && controlNotice && <p className="assistant-notice" role="status" aria-live="polite">{controlNotice}</p>}
      {adminView && <div className="work-controls"><button disabled={controlling || terminal || selected.status==="stopping"} onClick={()=>control("stop")}>{controlling ? "操作結果を確認中…" : ["stop_pending","rollback_stop_pending"].includes(selected.status) ? "停止を再確認する" : "作業を停止する"}</button><button className="quiet" disabled={controlling || ["rolled_back","rollback_stop_pending"].includes(selected.status)} onClick={()=>setConfirmRollback(true)}>開始前に戻す</button>{adminView && <button onClick={()=>control("refresh-usage")} disabled={busy}>料金を再取得</button>}</div>}
      {adminView && confirmRollback && <div className="rollback-confirmation" role="alert"><h3>この依頼の変更を、開始前へ戻しますか？</h3><p>処理を停止し、この依頼で保存した内容を戻します。履歴と利用料金は残ります。</p><div className="answer-options"><button className="danger" disabled={controlling} onClick={()=>control("rollback")}>開始前へ戻す</button><button disabled={controlling} onClick={()=>setConfirmRollback(false)}>キャンセル</button></div></div>}
      {adminView && <details className="technical-details"><summary>処理の詳細</summary><ol>{selected.events?.map(event=><li key={event.id}>{event.kind==="agent.message" ? event.payload.content?.filter((c:any)=>c.type==="text").map((c:any,i:number)=><p key={i}>{c.text}</p>) : event.kind==="tool_result" ? <p>{event.payload.tool}：{event.payload.result?.error || event.payload.result?.question || (event.payload.result?.ok===false ? "APIが処理を拒否しました" : "実行結果を記録しました")}</p> : event.kind==="completion_evidence" ? <p>{event.payload.summary}</p> : ["notice","error","operator_reply","rollback"].includes(event.kind) ? <p>{event.payload.message || event.payload.notice}</p> : null}</li>)}</ol></details>}
      <RunEventHistory key={selected.id} runId={selected.id} admin={adminView} />
    </section>}
    <details className="assistant-panel history-panel" open={adminView}><summary>作業の履歴 <span>{runs.length}件{history.hasNext?"（続きあり）":""}</span></summary>
      <label>表示する依頼<select value={history.unfinished?"unfinished":"all"} disabled={history.loading}
        onChange={event=>void history.filter(event.target.value==="unfinished").catch(showError)}><option value="all">すべて</option><option value="unfinished">未完了のみ</option></select></label>
      {!runs.length && <p className="muted">該当する依頼はありません。</p>}
      <ul>{runs.map((run:AssistantRunSummary)=><li key={run.id}><button className="history-row" disabled={busy} onClick={()=>openRun(run.id)}><span><strong>{run.instruction}</strong><small>{run.domains.map(id=>labels[id]).join(" ／ ")}</small></span><span className="status-tag">{adminView ? states[run.status] || run.status : businessLabels[businessProgress(run).state]}{adminView ? ` · ${run.provider==="gemini"?"Gemini・概算":"Claude・定価換算"} ${dollars(run.spent)}` : ""}</span></button></li>)}</ul>
      <button disabled={history.loading||!history.hasPrevious} onClick={()=>void history.previous().catch(showError)}>新しい100件</button>
      <button disabled={history.loading||!history.hasNext} onClick={()=>void history.next().catch(showError)}>以前の100件</button>
      <button disabled={history.loading} onClick={()=>void history.refresh(true).catch(showError)}>最新の依頼へ</button>
    </details>
    <p className="trial-footnote">試用中の変更は、この依頼専用のコピーに保存されます。</p>
  </main>;
}
export default function AssistantPage(){return <AssistantScreen/>;}
