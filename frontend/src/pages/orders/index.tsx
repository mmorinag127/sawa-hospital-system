import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/router";

import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidates,
  pickBestFacilityCandidate,
  type FacilityHint,
  type FacilityNameMap,
} from "../../services/facilityData";

type Order = {
  status: string;
  document: string;
  facility?: string | null;
  week?: string | null;
  week_value?: string | null;
  week_label?: string | null;
  candidate_resolution?: {
    resolutions?: {
      facility?: {
        resolved_value?: string | null;
        resolved_label?: string | null;
        candidates?: Array<{
          value?: string | null;
          label?: string | null;
          score?: number | null;
        }> | null;
      } | null;
      week?: {
        resolved_value?: string | null;
        resolved_label?: string | null;
      } | null;
    } | null;
  } | null;
  id?: string;
  received_at?: string | null;
  message_id?: string | null;
  ocr_status?: string | null;
  ocr_review_state?: string | null;
  ocr_review_badges?: string[] | null;
  ocr_has_saved_draft?: boolean | null;
  ocr_draft_newer_than_lines?: boolean | null;
  ocr_auto_apply_blocked?: boolean | null;
  ocr_reject_reasons?: string[] | null;
  ocr_processing_stage?: string | null;
  ocr_result_state?: string | null;
  ocr_confirmed_lines_retained?: boolean | null;
  archived_at?: string | null;
  archived_by?: string | null;
  is_archived?: boolean | null;
  workflow_state?: {
    state?: string | null;
    headline?: string | null;
    primary_action?: string | null;
    blockers_json?: string[] | null;
    warnings_json?: string[] | null;
  } | null;
  apply_gate?: {
    can_apply?: boolean | null;
    can_confirm?: boolean | null;
    blockers?: string[] | null;
    warnings?: string[] | null;
  } | null;
  ocr_pages_count?: number | null;
};

type WeekGroup = {
  key: string;
  label: string;
  sortKey: number;
  temporary: boolean;
  counts: Record<string, number>;
  missingFacilityCount: number;
  registeredFacilityCount: number;
  orders: Order[];
  facilitySlots: Array<{
    facilityId: string;
    facilityName: string;
    facilityIds: string[];
    orders: Order[];
  }>;
  unmatchedOrders: Order[];
};

type FacilityDisplayGroup = {
  key: string;
  label: string;
  facilityIds: string[];
};

const FACILITY_DISPLAY_GROUPS: FacilityDisplayGroup[] = [
  {
    key: "FAC-GRP-IKOI",
    label: "いこいの森 / いこいの森プラス",
    facilityIds: ["FAC00013", "FAC00016"],
  },
  {
    key: "FAC-GRP-SHIMANTO",
    label: "ケアハウス四万十 / ケアハウス四万十ピア",
    facilityIds: ["FAC00011", "FAC00015"],
  },
];

const FACILITY_DISPLAY_GROUP_BY_ID = new Map<string, FacilityDisplayGroup>(
  FACILITY_DISPLAY_GROUPS.flatMap((group) => group.facilityIds.map((facilityId) => [facilityId, group] as const)),
);

const compareOrdersByReceivedAt = (left: Order, right: Order) => {
  const leftTime = left.received_at ? new Date(left.received_at).getTime() : 0;
  const rightTime = right.received_at ? new Date(right.received_at).getTime() : 0;
  if (rightTime !== leftTime) return rightTime - leftTime;
  return String(right.id || "").localeCompare(String(left.id || ""), "ja");
};

const normalizeWeekGroup = (order: Order) => {
  const candidateWeek = order.candidate_resolution?.resolutions?.week;
  const candidateValue = String(candidateWeek?.resolved_value || "").trim();
  const candidateLabel = String(candidateWeek?.resolved_label || "").trim();
  const rawValue = candidateValue || String(order.week_value || order.week || "").trim();
  const rawLabel = candidateLabel || String(order.week_label || "").trim();
  const usesTemporaryWeek = Boolean(candidateValue || candidateLabel);
  const explicitRange = rawValue.match(/^(\d{4}-\d{2})@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
  if (explicitRange) {
    const label = rawLabel || `${explicitRange[2].slice(5).replace("-", "/")} - ${explicitRange[3].slice(5).replace("-", "/")}`;
    return {
      key: rawValue,
      label,
      sortKey: Date.parse(explicitRange[3]),
      temporary: usesTemporaryWeek,
    };
  }
  if (rawLabel) {
    return {
      key: rawValue || rawLabel,
      label: rawLabel,
      sortKey: Date.parse(`${String(rawValue || "").slice(0, 7)}-01`) || 0,
      temporary: usesTemporaryWeek || rawLabel.includes("("),
    };
  }
  if (rawValue) {
    return {
      key: rawValue,
      label: rawValue,
      sortKey: Date.parse(`${String(rawValue).slice(0, 7)}-01`) || 0,
      temporary: usesTemporaryWeek,
    };
  }
  return {
    key: "unresolved",
    label: "暫定週次未確定",
    sortKey: 0,
    temporary: false,
  };
};

const weekMenuId = (order: Order) => {
  const rawValue = String(
    order.candidate_resolution?.resolutions?.week?.resolved_value || order.week_value || order.week || "",
  ).trim();
  if (!rawValue) return "";
  if (rawValue.includes("@")) return rawValue.split("@", 1)[0];
  return rawValue.slice(0, 7);
};

const inlineFacilityHint = (order: Order): FacilityHint | null => {
  const facility = order.candidate_resolution?.resolutions?.facility;
  const resolvedValue = String(facility?.resolved_value || "").trim();
  const resolvedLabel = String(facility?.resolved_label || "").trim();
  if (resolvedValue && resolvedLabel) {
    return {
      order_id: String(order.id || ""),
      facility_id: resolvedValue,
      facility_name: resolvedLabel,
      score: null,
      reason: "orders_list_candidate_resolution",
      auto: null,
    };
  }
  const firstCandidate = Array.isArray(facility?.candidates) ? facility?.candidates?.[0] : null;
  const candidateValue = String(firstCandidate?.value || "").trim();
  const candidateLabel = String(firstCandidate?.label || "").trim();
  if (candidateValue && candidateLabel) {
    return {
      order_id: String(order.id || ""),
      facility_id: candidateValue,
      facility_name: candidateLabel,
      score: typeof firstCandidate?.score === "number" ? firstCandidate.score : null,
      reason: "orders_list_candidate_resolution",
      auto: null,
    };
  }
  return null;
};

const hasRegisteredFacility = (order: Order) => Boolean(String(order.facility || "").trim());

export default function OrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<Order[]>([]);
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [unresolvedOnly, setUnresolvedOnly] = useState<boolean>(false);
  const [showArchived, setShowArchived] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isHydratingRuntime, setIsHydratingRuntime] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string>("");
  const [archiveNotice, setArchiveNotice] = useState<string>("");
  const [archiveError, setArchiveError] = useState<string>("");
  const [reloadToken, setReloadToken] = useState<number>(0);
  const [expandedWeekGroups, setExpandedWeekGroups] = useState<Record<string, boolean>>({});
  const [archiveBusyWeek, setArchiveBusyWeek] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    fetchFacilityNameMap()
      .then((map) => {
        if (!cancelled) setFacilityNameMap(map);
      })
      .catch(() => {
        if (!cancelled) setFacilityNameMap({});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!router.isReady) return;
    const statusParam = router.query.status;
    const searchParam = router.query.search;
    const unresolvedParam = router.query.unresolved;
    setStatusFilter(typeof statusParam === "string" ? statusParam : "");
    setSearch(typeof searchParam === "string" ? searchParam : "");
    setUnresolvedOnly(
      typeof unresolvedParam === "string" ? unresolvedParam === "1" || unresolvedParam === "true" : false,
    );
  }, [router.isReady, router.query.status, router.query.search, router.query.unresolved]);

  useEffect(() => {
    let cancelled = false;
    const params = statusFilter
      ? { status: statusFilter, include_ocr: false, include_archived: showArchived, include_runtime: false }
      : { include_ocr: false, include_archived: showArchived, include_runtime: false };
    setIsLoading(true);
    setLoadError("");
    setIsHydratingRuntime(false);
    apiClient
      .get("/orders", { params })
      .then(async (res) => {
        if (cancelled) return;
        const baseOrders = res.data.orders || [];
        setOrders(baseOrders);
        setIsLoading(false);
        setIsHydratingRuntime(true);
        try {
          const runtimeParams = statusFilter
            ? { status: statusFilter, include_ocr: false, include_archived: showArchived }
            : { include_ocr: false, include_archived: showArchived };
          const runtimeRes = await apiClient.get("/orders", { params: runtimeParams });
          if (cancelled) return;
          const runtimeOrders = Array.isArray(runtimeRes.data?.orders) ? runtimeRes.data.orders : [];
          const runtimeById = new Map<string, Order>();
          runtimeOrders.forEach((runtimeOrder: Order) => {
            const orderId = String(runtimeOrder.id || "").trim();
            if (orderId) runtimeById.set(orderId, runtimeOrder);
          });
          setOrders((prev) =>
            prev.map((order) => {
              const orderId = String(order.id || "").trim();
              const runtimeOrder = runtimeById.get(orderId);
              return runtimeOrder ? { ...order, ...runtimeOrder } : order;
            }),
          );
        } catch {
          if (cancelled) return;
        } finally {
          if (!cancelled) setIsHydratingRuntime(false);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          "注文データの取得に失敗しました。";
        setLoadError(String(detail));
        setIsHydratingRuntime(false);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [statusFilter, reloadToken, showArchived]);

  useEffect(() => {
    let cancelled = false;
    const unresolved = orders
      .filter((order) => !order.facility && order.id && !inlineFacilityHint(order))
      .sort(compareOrdersByReceivedAt)
      .slice(0, 60)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !facilityHints[orderId]);

    if (unresolved.length === 0) return;

    const queue = [...unresolved];
    const concurrency = 4;
    const results: Record<string, FacilityHint> = {};

    const workers = Array.from({ length: concurrency }, async () => {
      while (queue.length > 0) {
        const orderId = queue.shift();
        if (!orderId) continue;
        try {
          const candidates = await fetchOrderFacilityCandidates(orderId);
          const best = pickBestFacilityCandidate(candidates);
          if (best) results[orderId] = { ...best, order_id: orderId };
        } catch {
          // ignore per-order failures
        }
      }
    });

    Promise.all(workers).then(() => {
      if (cancelled) return;
      if (Object.keys(results).length === 0) return;
      setFacilityHints((prev) => ({ ...prev, ...results }));
    });

    return () => {
      cancelled = true;
    };
  }, [orders, facilityHints]);

  const facilityLabel = (order: Order) => {
    const facilityId = order.facility || "";
    if (facilityId) {
      const name = facilityNameMap[facilityId];
      return name ? `${name} (${facilityId})` : facilityId;
    }
    const inlineHint = inlineFacilityHint(order);
    if (inlineHint?.facility_name) {
      const score = inlineHint.score != null ? ` / score=${inlineHint.score}` : "";
      return `推定: ${inlineHint.facility_name} (${inlineHint.facility_id}${score})`;
    }
    const orderId = order.id || "";
    const hint = orderId ? facilityHints[orderId] : null;
    if (hint?.facility_name) {
      const score = hint.score != null ? ` / score=${hint.score}` : "";
      return `推定: ${hint.facility_name} (${hint.facility_id}${score})`;
    }
    return "未確定";
  };

  const filteredOrders = useMemo(() => {
    return orders.filter((order) => {
      if (unresolvedOnly && order.facility) return false;
      if (!search) return true;
      const token = search.toLowerCase();
      const normalizedWeek = normalizeWeekGroup(order);
      const facilityId = order.facility || "";
      const facilityName = facilityId ? facilityNameMap[facilityId] || "" : "";
      const inlineHint = inlineFacilityHint(order);
      const inlineHintName = inlineHint?.facility_name ? String(inlineHint.facility_name) : "";
      const hint = order.id ? facilityHints[order.id] : null;
      const hintName = hint?.facility_name ? String(hint.facility_name) : "";
      return (
        (order.id || "").toLowerCase().includes(token) ||
        (order.message_id || "").toLowerCase().includes(token) ||
        facilityId.toLowerCase().includes(token) ||
        facilityName.toLowerCase().includes(token) ||
        inlineHintName.toLowerCase().includes(token) ||
        hintName.toLowerCase().includes(token) ||
        (order.week || "").toLowerCase().includes(token) ||
        normalizedWeek.key.toLowerCase().includes(token) ||
        normalizedWeek.label.toLowerCase().includes(token) ||
        (order.document || "").toLowerCase().includes(token)
      );
    });
  }, [facilityHints, facilityNameMap, orders, search, unresolvedOnly]);

  const sortedOrders = useMemo(() => [...filteredOrders].sort(compareOrdersByReceivedAt), [filteredOrders]);

  const resolvedFacilityIdForOrder = (order: Order) => {
    const confirmed = String(order.facility || "").trim();
    if (confirmed) return confirmed;
    const inlineHint = inlineFacilityHint(order);
    const inlineValue = String(inlineHint?.facility_id || "").trim();
    if (inlineValue) return inlineValue;
    const hinted = order.id ? facilityHints[order.id] : null;
    const hintedValue = String(hinted?.facility_id || "").trim();
    if (hintedValue) return hintedValue;
    return "";
  };

  const displayFacilityGroupForId = (facilityId: string): FacilityDisplayGroup => {
    const explicitGroup = FACILITY_DISPLAY_GROUP_BY_ID.get(facilityId);
    if (explicitGroup) return explicitGroup;
    return {
      key: facilityId,
      label: facilityNameMap[facilityId] || facilityId,
      facilityIds: [facilityId],
    };
  };

  const allFacilityDisplayGroups = useMemo(() => {
    const ids = new Set<string>(Object.keys(facilityNameMap));
    sortedOrders.forEach((order) => {
      const facilityId = resolvedFacilityIdForOrder(order);
      if (facilityId) ids.add(facilityId);
    });
    const groups = new Map<string, FacilityDisplayGroup>();
    Array.from(ids).forEach((facilityId) => {
      const group = displayFacilityGroupForId(facilityId);
      if (!groups.has(group.key)) groups.set(group.key, group);
    });
    return Array.from(groups.values()).sort((left, right) => left.label.localeCompare(right.label, "ja"));
  }, [facilityHints, facilityNameMap, sortedOrders]);

  const statusClass = (status?: string | null) => {
    switch (status) {
      case "未着":
        return "status-pending";
      case "要確認":
        return "status-review";
      case "確定":
        return "status-confirmed";
      case "エラー":
        return "status-error";
      default:
        return "";
    }
  };

  const reviewToneClass = (order: Order) => {
    const workflowState = String(order.workflow_state?.state || "").trim().toLowerCase();
    if (workflowState === "recovery_required") return "list-item-error";
    if (
      workflowState === "choice_required"
      || workflowState === "identity_choice_required"
      || workflowState === "layout_choice_required"
      || workflowState === "draft_ready"
      || workflowState === "draft_blocked"
      || workflowState === "review_required"
      || workflowState === "apply_ready"
    ) {
      return "list-item-review";
    }
    const reviewState = String(order.ocr_review_state || "").trim().toLowerCase();
    if (reviewState === "processing_failed") return "list-item-error";
    if (reviewState === "auto_apply_blocked" || reviewState === "draft_ready") return "list-item-review";
    return "";
  };

  const workflowStateLabel = (value?: string | null) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "uploaded") return "OCR待ち";
    if (normalized === "evidence_ready") return "証拠確認";
    if (normalized === "recovery_required") return "復旧待ち";
    if (normalized === "choice_required") return "選択待ち";
    if (normalized === "identity_choice_required") return "施設・週の選択待ち";
    if (normalized === "layout_choice_required") return "OCR候補の選択待ち";
    if (normalized === "draft_ready") return "下書き確認";
    if (normalized === "draft_blocked") return "反映前の確認待ち";
    if (normalized === "review_required") return "要確認";
    if (normalized === "apply_ready") return "反映可能";
    if (normalized === "confirmed") return "確定済み";
    return "";
  };

  const workflowPrimaryActionLabel = (order: Order) => {
    const explicit = String(order.workflow_state?.primary_action || "").trim();
    if (explicit) return explicit;
    const normalized = String(order.workflow_state?.state || "").trim().toLowerCase();
    if (normalized === "choice_required") return "候補を選ぶ";
    if (normalized === "identity_choice_required") return "施設・週を選ぶ";
    if (normalized === "layout_choice_required") return "OCR候補を選ぶ";
    if (normalized === "recovery_required") return "復旧する";
    if (normalized === "evidence_ready") return "OCR結果を確認";
    if (normalized === "draft_ready") return "下書きを確認";
    if (normalized === "draft_blocked") return "反映条件を解消";
    if (normalized === "review_required") return "要確認箇所を確認";
    if (normalized === "apply_ready") return "反映して確認";
    if (normalized === "confirmed") return "完了を確認";
    return "";
  };

  const visibleReviewBadges = (order: Order) => {
    const workflowBadge = workflowStateLabel(order.workflow_state?.state);
    const hasWorkflowState = Boolean(
      String(order.workflow_state?.state || "").trim()
      || String(order.workflow_state?.headline || "").trim(),
    );
    const hasWorkflowSummary = Boolean(
      String(order.workflow_state?.headline || "").trim() || workflowBadge,
    );
    const secondaryBadges = hasWorkflowState
      ? []
      : (order.ocr_review_badges || [])
          .map((badge) => String(badge || "").trim())
          .filter((badge) => badge && badge !== workflowBadge)
          .slice(0, hasWorkflowSummary ? 1 : 2);
    return {
      workflowBadge,
      secondaryBadges,
    };
  };

  const workflowSupportText = (order: Order) => {
    const blockers = Array.isArray(order.workflow_state?.blockers_json)
      ? order.workflow_state?.blockers_json?.map((item) => String(item || "").trim()).filter(Boolean) || []
      : [];
    const warnings = Array.isArray(order.workflow_state?.warnings_json)
      ? order.workflow_state?.warnings_json?.map((item) => String(item || "").trim()).filter(Boolean) || []
      : [];
    if (blockers.length) return `要対応 ${blockers.length}件`;
    if (warnings.length) return `確認 ${warnings.length}件`;
    return "";
  };

  const workflowHeadlineText = (order: Order) => {
    const explicitHeadline = String(order.workflow_state?.headline || "").trim();
    if (explicitHeadline) return explicitHeadline;
    const workflowBadge = workflowStateLabel(order.workflow_state?.state);
    if (workflowBadge) return workflowBadge;
    const primaryAction = workflowPrimaryActionLabel(order);
    if (primaryAction) return primaryAction;
    return "";
  };

  const processingStageLabel = (value?: string | null) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "ocr_pipeline") return "OCR準備";
    if (normalized === "inference") return "推論";
    if (normalized === "validation") return "検証";
    if (normalized === "draft_saved") return "下書き保存";
    if (normalized === "apply" || normalized === "applied") return "明細更新";
    return "";
  };

  const weekGroups = useMemo(() => {
    const groups = new Map<string, WeekGroup>();
    sortedOrders.forEach((order) => {
      const weekGroup = normalizeWeekGroup(order);
      const current =
        groups.get(weekGroup.key) || {
          ...weekGroup,
          counts: { 未着: 0, 要確認: 0, 確定: 0, エラー: 0 },
          missingFacilityCount: 0,
          registeredFacilityCount: 0,
          orders: [],
          facilitySlots: [],
          unmatchedOrders: [],
        };
      if (current.counts[order.status] !== undefined) current.counts[order.status] += 1;
      if (hasRegisteredFacility(order)) {
        current.registeredFacilityCount += 1;
      } else {
        current.missingFacilityCount += 1;
      }
      current.orders.push(order);
      groups.set(weekGroup.key, current);
    });
    groups.forEach((group) => {
      const facilityOrderMap = new Map<string, Order[]>();
      const unmatchedOrders: Order[] = [];
      group.orders.forEach((order) => {
        const facilityId = resolvedFacilityIdForOrder(order);
        if (!facilityId) {
          unmatchedOrders.push(order);
          return;
        }
        const displayGroup = displayFacilityGroupForId(facilityId);
        const bucket = facilityOrderMap.get(displayGroup.key) || [];
        bucket.push(order);
        facilityOrderMap.set(displayGroup.key, bucket);
      });
      group.facilitySlots =
        group.key === "unresolved"
          ? []
          : allFacilityDisplayGroups.map((facilityGroup) => ({
              facilityId: facilityGroup.key,
              facilityName: facilityGroup.label,
              facilityIds: facilityGroup.facilityIds,
              orders: facilityOrderMap.get(facilityGroup.key) || [],
            }));
      group.unmatchedOrders = unmatchedOrders.sort(compareOrdersByReceivedAt);
    });
    return Array.from(groups.values()).sort((left, right) => {
      if (left.key === "unresolved" && right.key !== "unresolved") return -1;
      if (right.key === "unresolved" && left.key !== "unresolved") return 1;
      if (right.sortKey !== left.sortKey) return right.sortKey - left.sortKey;
      return left.label.localeCompare(right.label, "ja");
    });
  }, [allFacilityDisplayGroups, facilityHints, facilityNameMap, sortedOrders]);

  const archivedCountForWeekGroup = (group: WeekGroup) =>
    group.orders.filter((order) => Boolean(order.is_archived)).length;

  const activeOrderIdsForWeekGroup = (group: WeekGroup) =>
    group.orders
      .filter((order) => !order.is_archived)
      .map((order) => String(order.id || "").trim())
      .filter(Boolean);

  const archivedOrderIdsForWeekGroup = (group: WeekGroup) =>
    group.orders
      .filter((order) => Boolean(order.is_archived))
      .map((order) => String(order.id || "").trim())
      .filter(Boolean);

  const canArchiveWeekGroup = (group: WeekGroup) => {
    if (group.key === "unresolved") return false;
    return activeOrderIdsForWeekGroup(group).length > 0;
  };

  const canUnarchiveWeekGroup = (group: WeekGroup) => {
    if (group.key === "unresolved") return false;
    return archivedOrderIdsForWeekGroup(group).length > 0;
  };

  const bulkArchivableWeekGroups = useMemo(
    () => weekGroups.filter((group) => canArchiveWeekGroup(group)),
    [weekGroups],
  );

  const bulkRestorableWeekGroups = useMemo(
    () => weekGroups.filter((group) => canUnarchiveWeekGroup(group)),
    [weekGroups],
  );

  const archiveWeekGroup = async (group: WeekGroup) => {
    const orderIds = activeOrderIdsForWeekGroup(group);
    if (!orderIds.length) return;
    if (!window.confirm(`「${group.label}」をアーカイブします。通常の注文一覧から除外されます。`)) {
      return;
    }
    setArchiveBusyWeek(group.key);
    setArchiveNotice("");
    setArchiveError("");
    try {
      await apiClient.post("/orders/archive-week", {
        week_value: group.key,
        order_ids: orderIds,
      });
      setArchiveNotice(`「${group.label}」をアーカイブしました。`);
      setReloadToken((value) => value + 1);
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail?.message
        || err?.response?.data?.detail?.error
        || err?.response?.data?.detail
        || err?.message
        || "週次アーカイブに失敗しました。";
      setArchiveError(String(detail));
    } finally {
      setArchiveBusyWeek("");
    }
  };

  const unarchiveWeekGroup = async (group: WeekGroup) => {
    const orderIds = archivedOrderIdsForWeekGroup(group);
    if (!orderIds.length) return;
    if (!window.confirm(`「${group.label}」のアーカイブを解除します。`)) {
      return;
    }
    setArchiveBusyWeek(group.key);
    setArchiveNotice("");
    setArchiveError("");
    try {
      await apiClient.post("/orders/unarchive-week", {
        week_value: group.key,
        order_ids: orderIds,
      });
      setArchiveNotice(`「${group.label}」を通常表示に戻しました。`);
      setReloadToken((value) => value + 1);
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail?.message
        || err?.response?.data?.detail?.error
        || err?.response?.data?.detail
        || err?.message
        || "アーカイブ解除に失敗しました。";
      setArchiveError(String(detail));
    } finally {
      setArchiveBusyWeek("");
    }
  };

  const archiveAllVisibleWeekGroups = async () => {
    if (bulkArchivableWeekGroups.length === 0) return;
    if (
      !window.confirm(
        `表示中の週次 ${bulkArchivableWeekGroups.length} 件を一括アーカイブします。通常の注文一覧から除外されます。`,
      )
    ) {
      return;
    }
    setArchiveBusyWeek("__bulk_archive__");
    setArchiveNotice("");
    setArchiveError("");
    const archivedLabels: string[] = [];
    const failures: string[] = [];
    try {
      for (const group of bulkArchivableWeekGroups) {
        const orderIds = activeOrderIdsForWeekGroup(group);
        if (!orderIds.length) continue;
        try {
          await apiClient.post("/orders/archive-week", {
            week_value: group.key,
            order_ids: orderIds,
          });
          archivedLabels.push(group.label);
        } catch (err: any) {
          const detail =
            err?.response?.data?.detail?.message
            || err?.response?.data?.detail?.error
            || err?.response?.data?.detail
            || err?.message
            || "週次アーカイブに失敗しました。";
          failures.push(`${group.label}: ${String(detail)}`);
        }
      }
      if (archivedLabels.length > 0) {
        setArchiveNotice(`${archivedLabels.length} 件の週次をアーカイブしました。`);
        setReloadToken((value) => value + 1);
      }
      if (failures.length > 0) {
        setArchiveError(failures.join(" / "));
      }
    } finally {
      setArchiveBusyWeek("");
    }
  };

  const unarchiveAllVisibleWeekGroups = async () => {
    if (bulkRestorableWeekGroups.length === 0) return;
    if (
      !window.confirm(
        `表示中のアーカイブ済み週次 ${bulkRestorableWeekGroups.length} 件を一括解除します。`,
      )
    ) {
      return;
    }
    setArchiveBusyWeek("__bulk_restore__");
    setArchiveNotice("");
    setArchiveError("");
    const restoredLabels: string[] = [];
    const failures: string[] = [];
    try {
      for (const group of bulkRestorableWeekGroups) {
        const orderIds = archivedOrderIdsForWeekGroup(group);
        if (!orderIds.length) continue;
        try {
          await apiClient.post("/orders/unarchive-week", {
            week_value: group.key,
            order_ids: orderIds,
          });
          restoredLabels.push(group.label);
        } catch (err: any) {
          const detail =
            err?.response?.data?.detail?.message
            || err?.response?.data?.detail?.error
            || err?.response?.data?.detail
            || err?.message
            || "アーカイブ解除に失敗しました。";
          failures.push(`${group.label}: ${String(detail)}`);
        }
      }
      if (restoredLabels.length > 0) {
        setArchiveNotice(`${restoredLabels.length} 件の週次を通常表示に戻しました。`);
        setReloadToken((value) => value + 1);
      }
      if (failures.length > 0) {
        setArchiveError(failures.join(" / "));
      }
    } finally {
      setArchiveBusyWeek("");
    }
  };

  const isWeekGroupExpanded = (groupKey: string) => expandedWeekGroups[groupKey] === true;

  const toggleWeekGroup = (groupKey: string) => {
    setExpandedWeekGroups((current) => ({
      ...current,
      [groupKey]: current[groupKey] !== true,
    }));
  };

  const renderOrderCard = (order: Order) => {
    const badges = visibleReviewBadges(order);
    const weekBucket = normalizeWeekGroup(order);
    return (
      <article key={order.id || order.document} className={`order-card ${reviewToneClass(order)}`.trim()}>
        <div className="order-card-top">
          <div>
            <p className="order-card-facility">{facilityLabel(order)}</p>
            <p className="order-card-title">{order.id || "注文ID未発行"}</p>
          </div>
          <span className={`status-pill ${statusClass(order.status)}`}>{order.status}</span>
        </div>
        <p className="order-card-week">{weekBucket.label}</p>
        {workflowHeadlineText(order) ? (
          <div className="list-workflow">
            <p className="list-workflow-title">{workflowHeadlineText(order)}</p>
            {workflowPrimaryActionLabel(order) && workflowPrimaryActionLabel(order) !== workflowHeadlineText(order) ? (
              <p className="list-workflow-action">次: {workflowPrimaryActionLabel(order)}</p>
            ) : null}
            {workflowSupportText(order) ? <p className="list-workflow-support">{workflowSupportText(order)}</p> : null}
          </div>
        ) : null}
        {badges.workflowBadge ||
        badges.secondaryBadges.length ||
        processingStageLabel(order.ocr_processing_stage) ||
        order.ocr_confirmed_lines_retained ? (
          <div className="review-badges">
            {badges.workflowBadge ? <span className="review-badge">{badges.workflowBadge}</span> : null}
            {badges.secondaryBadges.map((badge) => (
              <span className="review-badge" key={`${order.id || order.document}-${badge}`}>
                {badge}
              </span>
            ))}
            {!order.workflow_state && order.ocr_review_state === "processing" && processingStageLabel(order.ocr_processing_stage) ? (
              <span className="review-badge">{processingStageLabel(order.ocr_processing_stage)}</span>
            ) : null}
            {!order.workflow_state && order.ocr_confirmed_lines_retained ? (
              <span className="review-badge">確定明細保持</span>
            ) : null}
          </div>
        ) : null}
        <div className="list-actions">
          <Link href={`/orders/${order.id}`} className="list-link">
            詳細
          </Link>
          {weekMenuId(order) ? (
            <Link href={`/menus/${weekMenuId(order)}`} className="list-link">
              メニュー
            </Link>
          ) : null}
        </div>
      </article>
    );
  };

  const renderWeekGroup = (group: WeekGroup) => {
    const expanded = isWeekGroupExpanded(group.key);
    const bodyId = `week-group-body-${group.key.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
    const archivedCount = archivedCountForWeekGroup(group);
    return (
      <section
        key={group.key}
        className={`week-group${group.key === "unresolved" ? " week-group-unresolved" : ""}`}
      >
        <header className="week-group-header">
          <div>
            <p className="week-group-kicker">
              {group.key === "unresolved" ? "要確認の暫定グループ" : group.temporary ? "OCRの暫定週次" : "登録済み週次"}
            </p>
            <h3>{group.label}</h3>
            {group.missingFacilityCount > 0 ? (
              <p className="week-group-alert">
                {group.registeredFacilityCount === 0
                  ? "この週は施設未登録の注文だけです。"
                  : `施設未登録の注文が ${group.missingFacilityCount} 件あります。`}
              </p>
            ) : null}
          </div>
          <div className="week-group-header-actions">
            <div className="week-counts">
              {archivedCount > 0 ? (
                <span className="week-count week-count-archived">
                  アーカイブ済み {archivedCount}
                </span>
              ) : null}
              {Object.entries(group.counts)
                .filter(([, count]) => count > 0)
                .map(([label, count]) => (
                  <span className="week-count" key={`${group.key}-${label}`}>
                    {label} {count}
                  </span>
                ))}
            </div>
            <button
              type="button"
              className="week-group-toggle"
              aria-expanded={expanded}
              aria-controls={bodyId}
              onClick={() => toggleWeekGroup(group.key)}
            >
              {expanded ? "閉じる" : "開く"}
            </button>
            {group.key !== "unresolved" && canArchiveWeekGroup(group) ? (
              <button
                type="button"
                className="week-group-action week-group-action-archive"
                onClick={() => archiveWeekGroup(group)}
                disabled={archiveBusyWeek === group.key}
              >
                {archiveBusyWeek === group.key ? "処理中..." : "アーカイブ"}
              </button>
            ) : null}
            {group.key !== "unresolved" && canUnarchiveWeekGroup(group) ? (
              <button
                type="button"
                className="week-group-action week-group-action-restore"
                onClick={() => unarchiveWeekGroup(group)}
                disabled={archiveBusyWeek === group.key}
              >
                {archiveBusyWeek === group.key ? "処理中..." : "戻す"}
              </button>
            ) : null}
          </div>
        </header>
        {expanded ? (
          <div id={bodyId} className="week-group-body">
            <div className="order-card-grid">
              {group.orders.map((order) => renderOrderCard(order))}
            </div>
          </div>
        ) : null}
      </section>
    );
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Orders</p>
          <h1>注文一覧</h1>
          <p className="subtle">施設ごとの進捗と注文の状態を一覧で確認できます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">
            合計 {filteredOrders.length} 件
            {isHydratingRuntime ? " / 補足情報を取得中" : ""}
          </span>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">ステータス</span>
            <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">検索</span>
            <input
              className="input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="施設名 / 週 / 注文ID / 受付ID"
            />
          </label>
          <label className="field checkbox">
            <span className="field-label">施設未確定のみ</span>
            <input
              type="checkbox"
              checked={unresolvedOnly}
              onChange={(e) => setUnresolvedOnly(e.target.checked)}
            />
          </label>
          <label className="field checkbox">
            <span className="field-label">アーカイブ済みを表示</span>
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
          </label>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>週次ごとの注文</h2>
            <p className="subtle">OCRから読めた暫定週次で束ねています。ここでは受付IDと受信日時は省いています。</p>
          </div>
          <button className="ghost-link" type="button" onClick={() => setReloadToken((value) => value + 1)}>
            最新に更新
          </button>
        </header>
        {archiveNotice ? <p className="archive-feedback archive-feedback-success">{archiveNotice}</p> : null}
        {archiveError ? <p className="archive-feedback archive-feedback-error">{archiveError}</p> : null}
        {bulkArchivableWeekGroups.length > 0 || bulkRestorableWeekGroups.length > 0 ? (
          <div className="week-bulk-actions">
            {bulkArchivableWeekGroups.length > 0 ? (
              <button
                type="button"
                className="week-group-action week-group-action-archive"
                onClick={archiveAllVisibleWeekGroups}
                disabled={archiveBusyWeek === "__bulk_archive__" || archiveBusyWeek === "__bulk_restore__"}
              >
                {archiveBusyWeek === "__bulk_archive__"
                  ? "処理中..."
                  : `表示中の週次を一括アーカイブ (${bulkArchivableWeekGroups.length})`}
              </button>
            ) : null}
            {bulkRestorableWeekGroups.length > 0 ? (
              <button
                type="button"
                className="week-group-action week-group-action-restore"
                onClick={unarchiveAllVisibleWeekGroups}
                disabled={archiveBusyWeek === "__bulk_archive__" || archiveBusyWeek === "__bulk_restore__"}
              >
                {archiveBusyWeek === "__bulk_restore__"
                  ? "処理中..."
                  : `表示中の週次のアーカイブを解除 (${bulkRestorableWeekGroups.length})`}
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="week-groups">
          {isLoading ? (
            <p className="subtle">読み込み中...</p>
          ) : loadError ? (
            <div className="error-box">
              <p>{loadError}</p>
              <button className="retry-button" type="button" onClick={() => setReloadToken((value) => value + 1)}>
                再読み込み
              </button>
            </div>
          ) : sortedOrders.length === 0 ? (
            <p className="subtle">
              {orders.length === 0 ? "注文データがありません。" : "フィルタ条件に一致する注文がありません。"}
            </p>
          ) : (
            weekGroups.map((group) => renderWeekGroup(group))
          )}
        </div>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
        }

        :global(a) {
          color: inherit;
          text-decoration: none;
        }

        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }

        .hero {
          display: flex;
          flex-wrap: wrap;
          justify-content: space-between;
          gap: 24px;
          align-items: center;
          margin-bottom: 32px;
        }

        .eyebrow {
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-size: 12px;
          color: #5f7b74;
          margin-bottom: 8px;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }

        .subtle {
          color: #51615c;
          margin: 0;
        }

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          margin-bottom: 16px;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          white-space: nowrap;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
        }

        .field.checkbox {
          flex-direction: row;
          align-items: center;
          gap: 10px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }


        .error-box {
          border: 1px solid rgba(122, 47, 42, 0.25);
          background: #fceceb;
          color: #7a2f2a;
          padding: 12px 14px;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }

        .ghost-link {
          border: none;
          background: transparent;
          padding: 0;
          font-size: 13px;
          color: #5f7b74;
          cursor: pointer;
        }

        .ghost-link:hover {
          text-decoration: underline;
          text-underline-offset: 2px;
        }

        :global(.week-groups) {
          display: grid;
          gap: 18px;
        }

        :global(.week-group) {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 18px;
          background: linear-gradient(180deg, rgba(250, 247, 240, 0.65), rgba(255, 255, 255, 0.96));
          padding: 18px;
        }

        :global(.week-group-unresolved) {
          border: 1px solid rgba(61, 74, 71, 0.14);
          background:
            linear-gradient(180deg, rgba(248, 246, 242, 0.98), rgba(241, 237, 231, 0.98));
          box-shadow:
            inset 0 0 0 1px rgba(255, 255, 255, 0.7),
            0 16px 30px rgba(28, 36, 34, 0.08);
        }

        :global(.week-group-header) {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
        }

        :global(.week-group-header h3) {
          margin: 2px 0 0;
          font-size: 22px;
          line-height: 1.2;
        }

        :global(.week-group-kicker) {
          margin: 0;
          color: #6f7f79;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
        }

        :global(.week-group-unresolved .week-group-kicker) {
          color: #5d6b66;
        }

        :global(.week-group-unresolved .week-group-header h3) {
          color: #20302d;
        }

        :global(.week-group-alert) {
          margin: 8px 0 0;
          color: #7a4a1f;
          font-size: 13px;
          font-weight: 700;
        }

        :global(.week-group-header-actions) {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 10px;
        }

        :global(.week-group-body) {
          margin-top: 14px;
        }

        .archive-feedback {
          margin: 0 0 16px;
          padding: 10px 12px;
          border-radius: 12px;
          font-size: 14px;
        }

        .archive-feedback-success {
          background: #edf7f1;
          color: #204b34;
          border: 1px solid rgba(32, 75, 52, 0.12);
        }

        .archive-feedback-error {
          background: #fff2ef;
          color: #8b2d1f;
          border: 1px solid rgba(139, 45, 31, 0.16);
        }

        .week-bulk-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin: 0 0 16px;
        }

        :global(.week-counts) {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 8px;
        }

        :global(.week-count) {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          background: #eef2f0;
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
        }

        :global(.week-count-archived) {
          background: #eef1f7;
          color: #48536a;
        }

        :global(.week-group-unresolved .week-count) {
          background: #e8ecea;
          color: #31423f;
          border: 1px solid rgba(61, 74, 71, 0.1);
        }

        :global(.week-group-toggle),
        :global(.week-group-action) {
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #ffffff;
          color: #243330;
          padding: 8px 14px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 700;
          cursor: pointer;
          white-space: nowrap;
        }

        :global(.week-group-action-restore) {
          background: #f6f7fb;
        }

        :global(.week-group-toggle:hover),
        :global(.week-group-action:hover) {
          background: #f6f8f7;
        }

        :global(.week-group-action:disabled) {
          opacity: 0.6;
          cursor: wait;
        }

        .week-group-unresolved :global(.order-card),
        .week-group-unresolved :global(.order-card.list-item-review),
        .week-group-unresolved :global(.order-card.list-item-error) {
          background: #ffffff;
          border: 1px solid rgba(61, 74, 71, 0.12);
          box-shadow:
            0 14px 26px rgba(27, 35, 33, 0.08),
            0 2px 0 rgba(255, 255, 255, 0.72) inset;
        }

        .week-group-unresolved :global(.missing-order-card) {
          background: rgba(255, 255, 255, 0.98);
          border-color: rgba(61, 74, 71, 0.16);
        }

        .week-group-unresolved :global(.list-link) {
          background: rgba(243, 245, 244, 0.98);
        }

        .week-group-unresolved :global(.order-card-facility),
        .week-group-unresolved :global(.order-card-week) {
          color: #52625d;
        }

        .week-group-unresolved :global(.list-workflow) {
          background: rgba(246, 248, 247, 0.98);
          border-color: rgba(61, 74, 71, 0.08);
        }

        .facility-slot-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 14px;
        }

        .facility-slot {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 14px;
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: rgba(255, 255, 255, 0.92);
        }

        .facility-slot-missing {
          background: #faf7f1;
          border-style: dashed;
        }

        .facility-slot-unmatched {
          border-color: rgba(171, 125, 35, 0.28);
          background: #fffaf0;
        }

        .facility-slot-top {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
        }

        .facility-slot-kicker {
          margin: 0;
          color: #6f7f79;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
        }

        .facility-slot-name {
          margin: 4px 0 0;
          font-size: 18px;
          line-height: 1.25;
        }

        .facility-slot-badge {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          background: #eef2f0;
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
          white-space: nowrap;
        }

        .missing-order-card {
          border-radius: 12px;
          padding: 12px;
          border: 1px dashed rgba(122, 95, 49, 0.28);
          background: #fffdf8;
        }

        .missing-order-title {
          margin: 0;
          font-size: 14px;
          font-weight: 800;
          color: #5f4727;
        }

        .missing-order-text {
          margin: 6px 0 0;
          font-size: 12px;
          color: #6e5c43;
          line-height: 1.5;
        }

        :global(.order-card-grid) {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 12px;
        }

        :global(.order-card-grid--nested) {
          grid-template-columns: 1fr;
        }

        :global(.order-card) {
          display: flex;
          flex-direction: column;
          gap: 10px;
          min-height: 100%;
          padding: 14px;
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
          box-shadow: 0 10px 18px rgba(27, 35, 33, 0.04);
        }

        :global(.order-card.list-item-review) {
          border-color: rgba(171, 125, 35, 0.28);
          background: #fff8ef;
        }

        :global(.order-card.list-item-error) {
          border-color: rgba(148, 47, 44, 0.22);
          background: #fff2f1;
        }

        :global(.order-card-top) {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
        }

        :global(.order-card-facility) {
          margin: 0;
          color: #52625d;
          font-size: 12px;
          line-height: 1.5;
        }

        :global(.order-card-title) {
          margin: 4px 0 0;
          font-size: 18px;
          font-weight: 700;
          line-height: 1.25;
          word-break: break-word;
        }

        :global(.order-card-week) {
          margin: 0;
          color: #1f2a2a;
          font-size: 13px;
          font-weight: 700;
          letter-spacing: 0.01em;
        }

        :global(.list-workflow) {
          margin-top: 0;
          padding: 10px 11px;
          border-radius: 10px;
          background: #f4f7f6;
          border: 1px solid rgba(25, 32, 30, 0.06);
        }

        :global(.list-workflow-title) {
          margin: 0;
          font-size: 13px;
          font-weight: 600;
          color: #21302d;
        }

        :global(.list-workflow-action) {
          margin: 4px 0 0;
          font-size: 12px;
          color: #52625d;
        }

        :global(.list-workflow-support) {
          margin: 4px 0 0;
          font-size: 12px;
          color: #7a8783;
        }

        :global(.review-badges) {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-top: 8px;
        }

        :global(.review-badge) {
          display: inline-flex;
          align-items: center;
          padding: 3px 9px;
          border-radius: 999px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #f4f1ea;
          color: #31423f;
          font-size: 11px;
          font-weight: 600;
          white-space: nowrap;
        }

        :global(.list-actions) {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
          justify-content: flex-start;
          margin-top: auto;
        }

        .retry-button {
          border: none;
          background: #7a2f2a;
          color: #fff;
          padding: 6px 12px;
          border-radius: 999px;
          cursor: pointer;
          font-size: 12px;
          font-weight: 600;
        }

        :global(.status-pill) {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
          white-space: nowrap;
        }

        :global(.status-pill.status-pending) {
          background: #f6dfe6;
          color: #7a2f4b;
        }

        :global(.status-pill.status-review) {
          background: #f5e2c9;
          color: #7a4a1f;
        }

        :global(.status-pill.status-confirmed) {
          background: #dce8f5;
          color: #2f4f7a;
        }

        :global(.status-pill.status-error) {
          background: #f4dedb;
          color: #7a2f2a;
        }

        :global(.list-link) {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
          color: #1f2a2a;
          text-decoration: none;
          cursor: pointer;
        }

        .week-group-action:disabled {
          opacity: 0.6;
          cursor: wait;
        }

        :global(.list-link:hover) {
          background: #d8e0dd;
          text-decoration: underline;
          text-underline-offset: 2px;
        }

        @media (max-width: 720px) {
          :global(.week-group) {
            padding: 14px;
          }

          :global(.week-group-header) {
            flex-direction: column;
          }

          :global(.week-group-header-actions) {
            align-items: flex-start;
            width: 100%;
          }

          :global(.week-counts) {
            justify-content: flex-start;
          }

          :global(.order-card-grid) {
            grid-template-columns: 1fr;
          }

          :global(.order-card-top) {
            flex-direction: column;
          }

          :global(.status-pill) {
            align-self: flex-start;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
