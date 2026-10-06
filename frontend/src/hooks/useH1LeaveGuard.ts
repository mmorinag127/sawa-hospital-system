import { useEffect, useRef } from "react";
import { useNavigationBoundary } from "../components/NavigationBoundary";
import { hasActiveSessionAuthHeader } from "../services/apiClient";

export function useH1LeaveGuard(dirty: boolean, paths: string[]) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const boundary = useNavigationBoundary();
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  useEffect(() => boundary.register({
    active: () => hasActiveSessionAuthHeader() && dirtyRef.current,
    allow: (url) => {
      const path = new URL(url, window.location.origin).pathname;
      return paths.includes(path) || !hasActiveSessionAuthHeader() || !dirtyRef.current || window.confirm("未保存の変更を破棄して移動しますか？");
    },
  }), [boundary, paths]);
}
