import type { MouseEvent } from "react";

import { getStoredAuthHeader } from "./apiClient";

const SCHOOL_LUNCH_HOME = "/school-lunch/implementation-price-tables";
const SCHOOL_LUNCH_SESSION_ENDPOINT = "/school-lunch/api/backend/shared-auth/me";

export const enterSchoolLunch = async (
  event: MouseEvent<HTMLAnchorElement>,
  destination = SCHOOL_LUNCH_HOME,
) => {
  event.preventDefault();

  const authorization = getStoredAuthHeader();
  if (!authorization.startsWith("Bearer ")) {
    window.sessionStorage.setItem("auth_next", destination);
    window.location.assign("/login");
    return;
  }

  const response = await fetch(SCHOOL_LUNCH_SESSION_ENDPOINT, {
    method: "GET",
    headers: { Authorization: authorization },
    credentials: "same-origin",
    cache: "no-store",
  });

  if (response.ok) {
    window.location.assign(destination);
    return;
  }

  if (response.status === 401) {
    window.sessionStorage.setItem("auth_next", destination);
    window.location.assign("/login");
    return;
  }

  window.location.assign("/");
};
