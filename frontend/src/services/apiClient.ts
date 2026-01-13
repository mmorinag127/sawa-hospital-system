import axios, { AxiosHeaders, type AxiosRequestHeaders } from "axios";

const AUTH_STORAGE_KEY = "auth_header";
const AUTH_COOKIE_KEY = "auth_header";
const AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 14;

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

export const getStoredAuthHeader = () => {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(AUTH_STORAGE_KEY) || readCookieValue(AUTH_COOKIE_KEY);
};

const setStoredAuthHeader = (value: string) => {
  if (typeof window === "undefined") return;
  if (!value) {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    document.cookie = `${AUTH_COOKIE_KEY}=; Path=/; Max-Age=0; SameSite=Lax`;
    return;
  }
  window.localStorage.setItem(AUTH_STORAGE_KEY, value);
  document.cookie = `${AUTH_COOKIE_KEY}=${encodeURIComponent(
    value
  )}; Path=/; Max-Age=${AUTH_COOKIE_MAX_AGE}; SameSite=Lax`;
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

const fallbackBaseUrl =
  typeof window === "undefined" ? "http://localhost:8000" : "/api";

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL || fallbackBaseUrl,
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
