/** Return only an internal UI location; never use an unvalidated next as a URL. */
export function validateLoginDestination(value: unknown): string | null {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value, "https://sawa.invalid");
    if (url.origin !== "https://sawa.invalid") return null;
    const pathname = decodeURIComponent(url.pathname);
    if (pathname.startsWith("//") || /[\\\u0000-\u0020\u007f]/.test(pathname) || /%(?:2f|5c|2e|25)/i.test(pathname)) return null;
    if (/(?:^|\/)\.{1,2}(?:\/|$)/.test(pathname)) return null;
    if (/(?:^|\/)(?:api|_next)(?:\/|$)/.test(pathname) || /^\/(?:login|logout|auth)(?:\/|$)/.test(pathname) || /^\/school-lunch\/auth(?:\/|$)/.test(pathname)) return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}

export function resolveLoginDestination(search: string, stored: string | null): string {
  const values = new URLSearchParams(search).getAll("next");
  const destination = values.length === 0 ? (stored || "/") : values.length === 1 ? values[0] : null;
  const validated = validateLoginDestination(destination);
  if (!validated) throw new Error("復帰先が不正です。統合トップから開き直してください。");
  return validated;
}

export function loginUrlFor(destination: string): string {
  const validated = validateLoginDestination(destination);
  if (!validated) throw new Error("復帰先が不正です。");
  return `/login?${new URLSearchParams({next: validated})}`;
}
