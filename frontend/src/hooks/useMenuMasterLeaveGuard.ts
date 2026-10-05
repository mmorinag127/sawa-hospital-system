import { useEffect, useRef } from "react";
import { useNavigationBoundary } from "../components/NavigationBoundary";
import { hasActiveSessionAuthHeader } from "../services/apiClient";

type Options = { dirty: boolean };

const confirmDiscard = () => window.confirm("未保存の変更を破棄して移動しますか？");

export function useMenuMasterLeaveGuard({ dirty }: Options) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const boundary = useNavigationBoundary();

  useEffect(() => {
    return boundary.register({
      active: () => hasActiveSessionAuthHeader() && dirtyRef.current,
      allow: url => {
        const path = new URL(url, window.location.origin).pathname;
        const sameScreen = path === "/menu-masters" || path === "/hospital/menu-masters";
        return !hasActiveSessionAuthHeader() || sameScreen || !dirtyRef.current || confirmDiscard();
      },
    });
  }, [boundary, dirty]);
}
