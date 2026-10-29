import type { CanonicalFacilityResolution, FacilityOption, WorkflowRevisionToken, WorkflowV2 } from "./workflowTypes";

export const resolveCanonicalFacility = (
  facilityId: string,
  facilityOptions: FacilityOption[],
  loading: boolean,
  error: string,
): CanonicalFacilityResolution => {
  if (loading) return { status: "pending", option: null };
  if (error) return { status: "error", option: null };
  const normalizedFacilityId = facilityId.trim();
  if (!normalizedFacilityId) return { status: "missing", option: null };
  const matches = facilityOptions.filter((option) => option.id === normalizedFacilityId);
  if (matches.length === 1) return { status: "ready", option: matches[0] };
  if (matches.length > 1) return { status: "ambiguous", option: null };
  return { status: "unregistered", option: null };
};

export const canonicalFacilityResolutionMessage = (resolution: CanonicalFacilityResolution) => {
  switch (resolution.status) {
    case "pending":
      return "正本の施設一覧を取得中です。取得完了まで施設の反映・保存・OCR実行はできません。";
    case "error":
      return "正本の施設一覧を取得できません。施設の反映・保存・OCR実行はできません。";
    case "missing":
      return "正本の施設を選択してください。";
    case "unregistered":
      return "この施設IDは正本の施設一覧に未登録です。内容を確認するまで保存・OCR実行はできません。";
    case "ambiguous":
      return "この施設IDは正本の施設一覧で一意に照合できません。内容を確認するまで保存・OCR実行はできません。";
    default:
      return "";
  }
};

export const isPositiveWorkflowRevision = (value: unknown): value is number => (
  typeof value === "number" && Number.isInteger(value) && value > 0
);

export const sameRecordedValue = (left: unknown, right: unknown): boolean => {
  if (left===right) return true;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length===right.length && left.every((value,index)=>sameRecordedValue(value,right[index]));
  if (!left || !right || typeof left!=="object" || typeof right!=="object") return false;
  const a=left as Record<string,unknown>, b=right as Record<string,unknown>;
  return Object.keys(a).length===Object.keys(b).length
    && Object.keys(a).every(key=>Object.prototype.hasOwnProperty.call(b,key) && sameRecordedValue(a[key],b[key]));
};

export const sameQuadOverrideSource = (saved: unknown, selected: Record<string,unknown>) => {
  if (!saved || typeof saved!=="object" || Array.isArray(saved)) return false;
  const receipt=saved as Record<string,unknown>;
  return ["selected_pdf_receipt","selected_version_input","geometry_source_digest","configured_input_digest","order_input_identity"]
    .every(key=>Object.prototype.hasOwnProperty.call(receipt,key) && Object.prototype.hasOwnProperty.call(selected,key)
      && sameRecordedValue(receipt[key],selected[key]))
    && typeof receipt.geometry_source_digest==="string" && Boolean(receipt.geometry_source_digest)
    && typeof receipt.configured_input_digest==="string" && Boolean(receipt.configured_input_digest);
};

export const workflowRevisionTokenFromRead = (workflow: WorkflowV2): WorkflowRevisionToken | null => {
  if (isPositiveWorkflowRevision(workflow.revision)) {
    return { revision: workflow.revision, bootstrap: false };
  }
  if (
    workflow.revision === 0
    && workflow.state === "not_initialized"
    && workflow.source === "workflow_v2_projection"
  ) {
    return { revision: 0, bootstrap: true };
  }
  return null;
};
