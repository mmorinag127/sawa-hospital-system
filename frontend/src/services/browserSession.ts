// Same-origin logout protocol shared by the independently deployed applications.
const LOGOUT_KEY = "sawa_logout_generation";
const SESSION_KEY = "sawa_auth_generation";

const generation = () => window.localStorage.getItem(LOGOUT_KEY) || "";

export const sessionWasLoggedOut = () =>
  typeof window !== "undefined" && !!generation() &&
  window.sessionStorage.getItem(SESSION_KEY) !== generation();

export const markSessionCurrent = () => {
  window.sessionStorage.setItem(SESSION_KEY, generation());
};

export const clearBrowserSession = () => {
  window.sessionStorage.removeItem("auth_header");
  window.sessionStorage.removeItem("auth_next");
  window.sessionStorage.removeItem(SESSION_KEY);
  window.localStorage.removeItem("auth_header");
  document.cookie = "auth_header=; Path=/; Max-Age=0; SameSite=Lax";
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
