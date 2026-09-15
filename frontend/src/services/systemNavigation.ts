import type { MouseEvent } from "react";

import { getStoredAuthHeader } from "./apiClient";
import { loginUrlFor, validateLoginDestination } from "./loginDestination";

const SCHOOL_LUNCH_HOME = "/school-lunch/implementation-price-tables";
const SCHOOL_LUNCH_SESSION_ENDPOINT = "/school-lunch/api/backend/shared-auth/me";

export class LoginNavigationError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function prepareSystemDestination(destination: string, authorization: string, signal?: AbortSignal): Promise<string> {
  const target = validateLoginDestination(destination);
  if (!target) throw new LoginNavigationError(400, "復帰先が不正です。統合トップから開き直してください。");
  if (!authorization.startsWith("Bearer ") || !authorization.slice(7).trim()) throw new LoginNavigationError(401, "Googleでログインしてください。");
  const pathname = new URL(target, "https://sawa.invalid").pathname;
  const lunch = pathname === "/school-lunch" || pathname.startsWith("/school-lunch/");
  const system = pathname === "/shift" || pathname.startsWith("/shift/") ? "shift" : pathname === "/hospital" || pathname.startsWith("/hospital/") ? "hospital" : "";
  let response: Response;
  try {
    response = await fetch(lunch ? SCHOOL_LUNCH_SESSION_ENDPOINT : `/api/portal/auth/me${system ? `?system=${system}` : ""}`, {
      method: "GET", headers: { Authorization: authorization }, credentials: "same-origin", cache: "no-store", redirect: "error", signal,
    });
  } catch {
    throw new LoginNavigationError(503, "認証サービスに接続できません。時間をおいてもう一度お試しください。");
  }
  if (!response.ok) throw new LoginNavigationError(response.status,
    response.status === 401 ? "ログインの有効期限が切れています。Googleでログインし直してください。" :
    response.status === 403 ? "このシステムの利用権限がありません。統合管理者に確認してください。" :
    "認証サービスを確認できません。時間をおいてもう一度お試しください。");
  let identity: {role?: string; auth_disabled?: boolean};
  try { identity = await response.json(); } catch { throw new LoginNavigationError(503, "認証サービスの応答を確認できませんでした。"); }
  if (!identity || identity.auth_disabled || !["admin", "operator"].includes(identity.role || "")) throw new LoginNavigationError(403, "利用者の権限を確認できませんでした。");
  return target;
}

export const enterSchoolLunch = async (
  event: MouseEvent<HTMLAnchorElement>,
  destination = SCHOOL_LUNCH_HOME,
) => {
  event.preventDefault();

  const authorization = getStoredAuthHeader();
  try {
    const target = await prepareSystemDestination(destination, authorization);
    window.location.assign(target);
  } catch (error) {
    if (error instanceof LoginNavigationError && error.status === 401) {
      const login = loginUrlFor(destination);
      window.sessionStorage.setItem("auth_next", destination);
      window.location.assign(login);
    } else {
      window.alert(error instanceof Error ? error.message : "システムを開けませんでした。");
    }
  }
};
