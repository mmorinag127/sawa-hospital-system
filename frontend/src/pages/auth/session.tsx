import { useEffect, useState } from "react";
import { establishServerSession } from "../../services/apiClient";
import { resolveLoginDestination } from "../../services/loginDestination";
import { prepareSystemDestination } from "../../services/systemNavigation";
import { watchBrowserLogout } from "../../services/browserSession";

export default function SessionLanding() {
  const [message, setMessage] = useState("ログインを確認しています。");
  useEffect(() => {
    const controller = new AbortController();
    const stopWatching = watchBrowserLogout(() => controller.abort());
    void (async () => {
      const next = resolveLoginDestination(window.location.search, null);
      await establishServerSession(controller.signal);
      const destination = await prepareSystemDestination(next, "", controller.signal);
      if (!controller.signal.aborted) {
        window.sessionStorage.removeItem("auth_next");
        window.location.replace(destination);
      }
    })().catch(() => { if (!controller.signal.aborted) setMessage("ログインまたは利用権限を確認できませんでした。"); });
    return () => { controller.abort(); stopWatching(); };
  }, []);
  return <p role="status">{message}</p>;
}
