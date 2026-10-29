export type AssistantEventPage = {
  next_event: number;
  previous_event: number | null;
  has_previous: boolean;
  has_more: boolean;
};

// This is a bounded response buffer, not a substitute for canonical server state.
export function mergeAssistantHistory<T extends { id: number | string }>(
  previous: readonly T[] | undefined, incoming: readonly T[] | undefined,
): T[] {
  const records = new Map((previous ?? []).map(item => [String(item.id), item]));
  for (const item of incoming ?? []) records.set(String(item.id), item);
  return [...records.values()].sort((left, right) => Number(left.id) - Number(right.id)).slice(-500);
}
