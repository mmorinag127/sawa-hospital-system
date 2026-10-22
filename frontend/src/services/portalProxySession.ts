// Request headers only: never attach credentials to a browser response.
export function portalProxyHeaders(path: string, method: string, headers: Headers, cookies: Record<string, string>, origin: string) {
  const result = new Headers(headers);
  const authorization = headers.get("authorization") || "";
  if (authorization && !authorization.startsWith("Bearer ps_")) return result;
  const session = cookies["__Host-sawa_session"] || "";
  const schoolToken = cookies.sawa_school_lunch_auth || "";
  const school = path.startsWith("/school-lunch/");
  const bridge = path === "/school-lunch/api/backend/shared-auth/me";
  const shift = path.startsWith("/shift/api/");
  const assistant = path === "/api/assistant-trial" || path.startsWith("/api/assistant-trial/");
  const api = path.startsWith("/api/") || school || shift;
  if (!api || path === "/api/portal/auth/callback") return result;
  const opaque = /^ps_[A-Za-z0-9_-]{43}$/.test(session) || (school && schoolToken.startsWith("ps_")) || authorization.startsWith("Bearer ps_");
  if (!opaque) return result;
  if (!origin || new URL(origin).origin !== origin || !origin.startsWith("https://")) throw new Error("portal_origin_unavailable");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = headers.get("x-sawa-csrf");
    const generation = headers.get("x-sawa-session-generation");
    const schoolGeneration = school && !bridge && !!generation && generation === cookies.sawa_school_lunch_session_generation;
    if (headers.get("origin") !== origin || (!schoolGeneration && (!csrf || csrf !== cookies["__Host-sawa_csrf"]))) {
      throw new Error("portal_csrf_rejected");
    }
  }
  if (bridge && method === "GET") {
    if (headers.get("sec-fetch-site") !== "same-origin" && headers.get("origin") !== origin) throw new Error("portal_csrf_rejected");
    const schoolSessionCurrent = schoolToken === session && !!cookies.sawa_school_lunch_session_generation;
    if (!authorization && /^ps_[A-Za-z0-9_-]{43}$/.test(session) && !schoolSessionCurrent) {
      result.set("authorization", `Bearer ${session}`);
    }
  } else if ((shift || assistant) && /^ps_[A-Za-z0-9_-]{43}$/.test(session)) {
    result.set("authorization", `Bearer ${session}`);
  }
  return result;
}
