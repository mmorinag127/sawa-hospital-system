import { useEffect, useRef, useState } from 'react';
import Router, { useRouter } from 'next/router';
import { useNavigationBoundary } from '../../src/components/NavigationBoundary';
import { useH1LeaveGuard } from '../../src/hooks/useH1LeaveGuard';
import { useMenuMasterLeaveGuard } from '../../src/hooks/useMenuMasterLeaveGuard';
import { useH1EditorOwner } from '../../src/hooks/useH1EditorOwner';

const rawPush = typeof window === 'undefined' ? null : history.pushState.bind(history);
const rawReplace = typeof window === 'undefined' ? null : history.replaceState.bind(history);
function H1({ dirty }: { dirty: boolean }) { useH1LeaveGuard(dirty, ['/allowed']); return null; }
function Menu({ dirty }: { dirty: boolean }) { useMenuMasterLeaveGuard({ dirty }); return null; }

export default function Editor() {
  const router = useRouter();
  const boundary: any = process.env.NEXT_PUBLIC_BOUNDARY_MODE === 'bare' ? null : useNavigationBoundary();
  const [mode, setMode] = useState('none');
  const [text, setText] = useState('');
  const [saved, setSaved] = useState('');
  const owner = useH1EditorOwner(0);
  const lease = useRef<ReturnType<typeof owner.start> | null>(null);
  const events = useRef<string[]>([]);
  const scrolls = useRef<unknown[]>([]);
  useEffect(() => {
    const stats = (window as any).effectReplay ||= { setup: 0, cleanup: 0 };
    stats.setup++;
    return () => { stats.cleanup++; };
  }, []);
  useEffect(() => {
    const onStart = (url: string) => events.current.push(`start:${url}`);
    const onComplete = (url: string) => events.current.push(`complete:${url}`);
    const onError = (error: Error, url: string) => events.current.push(`error:${url}:${error.message}`);
    router.events.on('routeChangeStart', onStart);
    router.events.on('routeChangeComplete', onComplete);
    router.events.on('routeChangeError', onError);
    const scroll = window.scrollTo;
    window.scrollTo = ((...args: any[]) => { scrolls.current.push(args); (scroll as any).apply(window, args); }) as typeof window.scrollTo;
    return () => {
      router.events.off('routeChangeStart', onStart);
      router.events.off('routeChangeComplete', onComplete);
      router.events.off('routeChangeError', onError);
      window.scrollTo = scroll;
    };
  }, [router.events]);
  useEffect(() => {
    (window as any).driver = {
      ready: router.isReady,
      idle: () => !(Router as any).router.clc,
      mode: setMode,
      push: (as: string) => router.push(router.pathname, as, { shallow: true }),
      replace: (as: string) => router.replace(router.pathname, as, { shallow: true }),
      allowedPush: async (as: string) => boundary!.allow(as) ? router.push(router.pathname, as, { shallow: true }) : false,
      allow: (as: string) => boundary!.allow(as),
      beginLease: () => { lease.current = owner.start(); },
      seedUnknown: (state: unknown) => {
        const owned = history.state;
        rawReplace!(state, '', '/unknown');
        rawPush!(owned, '', router.asPath);
      },
      clearTrace: () => { events.current = []; scrolls.current = []; },
      snapshot: () => ({ path: location.pathname, state: history.state, length: history.length,
        routerKey: (Router as any).router._key, route: router.asPath, query: router.query,
        events: events.current, scrolls: scrolls.current, scrollY: window.scrollY, lease: lease.current?.current() }),
    };
  });
  return <main style={{ minHeight: 4000 }}>
    {mode === 'h1' && <H1 dirty={text !== saved} />}{mode === 'menu' && <Menu dirty={text !== saved} />}
    <input aria-label="draft" value={text} onChange={event => { owner.edit(); setText(event.target.value); }} />
    <button onClick={() => setSaved(text)}>save</button><output>{router.asPath}</output>
  </main>;
}
