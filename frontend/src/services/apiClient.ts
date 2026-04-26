import axios, { AxiosHeaders, type AxiosRequestHeaders } from "axios";

const AUTH_STORAGE_KEY = "auth_header";
const LEGACY_AUTH_COOKIE_KEY = "auth_header";

const readCookieValue = (key: string) => {
  if (typeof document === "undefined") return "";
  const parts = document.cookie.split(";").map((part) => part.trim());
  const prefix = `${key}=`;
  for (const part of parts) {
    if (part.startsWith(prefix)) {
      return decodeURIComponent(part.slice(prefix.length));
    }
  }
  return "";
};

const clearLegacyAuthStorage = () => {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(AUTH_STORAGE_KEY);
  document.cookie = `${LEGACY_AUTH_COOKIE_KEY}=; Path=/; Max-Age=0; SameSite=Lax`;
};

const getSessionAuthHeader = () => {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(AUTH_STORAGE_KEY) || "";
};

export const getStoredAuthHeader = () => {
  if (typeof window === "undefined") return "";
  const sessionValue = getSessionAuthHeader();
  if (sessionValue) {
    return sessionValue;
  }

  const legacyValue =
    window.localStorage.getItem(AUTH_STORAGE_KEY) || readCookieValue(LEGACY_AUTH_COOKIE_KEY);
  if (!legacyValue) {
    return "";
  }

  // Migrate old bearer sessions once, but always drop legacy persistence.
  if (legacyValue.startsWith("Bearer ")) {
    window.sessionStorage.setItem(AUTH_STORAGE_KEY, legacyValue);
  }
  clearLegacyAuthStorage();
  return legacyValue.startsWith("Bearer ") ? legacyValue : "";
};

const setStoredAuthHeader = (value: string) => {
  if (typeof window === "undefined") return;
  if (!value) {
    window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
    clearLegacyAuthStorage();
    return;
  }
  window.sessionStorage.setItem(AUTH_STORAGE_KEY, value);
  clearLegacyAuthStorage();
};

export const setBasicAuth = (token: string) => {
  setStoredAuthHeader(token ? `Basic ${token}` : "");
};

export const setBearerToken = (token: string) => {
  setStoredAuthHeader(token ? `Bearer ${token}` : "");
};

export const clearAuth = () => {
  setStoredAuthHeader("");
};

const inferBaseUrl = () => {
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
  }
  const origin = window.location.origin;
  if (origin.includes("web-prod") || origin.includes("web-stg") || origin.includes(".run.app")) {
    return "/api";
  }
  const envBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  const allowDirectBrowserApi = process.env.NEXT_PUBLIC_ALLOW_DIRECT_BROWSER_API === "1";
  const isLocalOrigin =
    origin.includes("localhost") || origin.includes("127.0.0.1") || origin.includes("[::1]");
  if (allowDirectBrowserApi && envBase && envBase !== "/api" && isLocalOrigin) {
    return envBase;
  }
  if (allowDirectBrowserApi && origin.includes("web-dev") && isLocalOrigin) {
    return origin.replace("web-dev", "worker-dev");
  }
  return "/api";
};

export const apiClient = axios.create({
  baseURL: inferBaseUrl(),
  timeout: 30000,
});

apiClient.interceptors.request.use((config) => {
  const header = getStoredAuthHeader();
  if (header) {
    const headers = AxiosHeaders.from(config.headers || ({} as AxiosRequestHeaders));
    headers.set("Authorization", header);
    config.headers = headers;
  }
  return config;
});

apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err?.response?.status === 401 && typeof window !== "undefined") {
      clearAuth();
      if (!window.location.pathname.startsWith("/login")) {
        window.sessionStorage.setItem("auth_next", window.location.pathname);
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  }
);
