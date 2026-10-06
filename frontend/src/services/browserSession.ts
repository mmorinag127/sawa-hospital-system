// Same-origin logout protocol shared by the independently deployed applications.
const LOGOUT_KEY = "sawa_logout_generation";
const SESSION_KEY = "sawa_auth_generation";
const CACHE_KEY = "sawa_auth_cache_generation";
const SESSION_EVENT = "sawa:auth-generation";
const SCHOOL_GENERATION_KEY = "sawa_school_lunch_session_generation";
const SCHOOL_PUBLISHED_GENERATION_KEY = "sawa_school_lunch_published_generation";

export const publishSchoolLunchSessionGeneration = (value: unknown) => {
  if (typeof value !== "string" || !value) throw new Error("School lunch session generation is required");
  window.sessionStorage.setItem(SCHOOL_GENERATION_KEY, value);
  window.localStorage.setItem(SCHOOL_PUBLISHED_GENERATION_KEY, value);
};

const notifySessionChange = () => {
  if (typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new Event(SESSION_EVENT));
  }
};

/** A non-secret cache identity. It changes only when authentication changes. */
export const browserSessionKey = () =>
  typeof window === "undefined" ? "server" : window.sessionStorage.getItem(CACHE_KEY) || "anonymous";

export const subscribeBrowserSession = (listener: () => void) => {
  window.addEventListener(SESSION_EVENT, listener);
  return () => window.removeEventListener(SESSION_EVENT, listener);
};

const generation = () => window.localStorage.getItem(LOGOUT_KEY) || "";

export const sessionWasLoggedOut = () =>
  typeof window !== "undefined" && !!generation() &&
  window.sessionStorage.getItem(SESSION_KEY) !== generation();

export const markSessionCurrent = () => {
  window.sessionStorage.setItem(SESSION_KEY, generation());
};

export const markAuthCacheChanged = () => {
  window.sessionStorage.setItem(CACHE_KEY, window.crypto.randomUUID());
  notifySessionChange();
};

export const clearBrowserSession = () => {
  window.sessionStorage.removeItem("auth_header");
  window.sessionStorage.removeItem("auth_next");
  window.sessionStorage.removeItem(SESSION_KEY);
  window.sessionStorage.removeItem(CACHE_KEY);
  window.localStorage.removeItem("auth_header");
  document.cookie = "auth_header=; Path=/; Max-Age=0; SameSite=Lax";
  notifySessionChange();
};

export const broadcastLogout = () => {
  clearBrowserSession();
  window.localStorage.setItem(LOGOUT_KEY, window.crypto.randomUUID());
};

export const watchBrowserLogout = (onLogout: () => void) => {
  let observed = generation();
  const check = () => {
    const current = generation();
    if (current === observed) return;
    observed = current;
    clearBrowserSession();
    onLogout();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === LOGOUT_KEY) check();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener("pageshow", check);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener("pageshow", check);
  };
};
