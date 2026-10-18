import { useEffect, useRef } from "react";
import { useNavigationBoundary } from "../components/NavigationBoundary";
import { hasActiveSessionAuthHeader } from "../services/apiClient";

export function useH1LeaveGuard(dirty: boolean | (() => boolean), paths: string[]) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const boundary = useNavigationBoundary();
  const isDirty = () => typeof dirtyRef.current === "function" ? dirtyRef.current() : dirtyRef.current;
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasActiveSessionAuthHeader() || !isDirty()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  useEffect(() => boundary.register({
    active: () => hasActiveSessionAuthHeader() && isDirty(),
    allow: (url) => {
      const path = new URL(url, window.location.origin).pathname;
      return paths.includes(path) || !hasActiveSessionAuthHeader() || !isDirty() || window.confirm("未保存の変更を破棄して移動しますか？");
    },
  }), [boundary, paths]);
  return boundary;
}
