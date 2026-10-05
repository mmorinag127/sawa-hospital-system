import { useSyncExternalStore } from "react";
import { useRouter } from "next/router";

const subscribe = () => () => {};
const client = () => true;
const server = () => false;

export function useHydratedPath() {
  const router = useRouter();
  const hydrated = useSyncExternalStore(subscribe, client, server);
  return hydrated ? router.asPath : "";
}
