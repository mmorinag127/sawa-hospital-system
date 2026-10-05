export const menuMasterUnits = [
  { value: "g", label: "グラム (g)" },
  { value: "cut", label: "切れ" },
  { value: "count", label: "個" },
] as const;

export const menuMasterTemperatures = [
  { value: "hot", label: "温" },
  { value: "cold", label: "冷" },
] as const;

export function menuMasterOptionLabel(
  options: readonly { value: string; label: string }[],
  value: string | null,
): string {
  if (value === null || value === "") return "—";
  return options.find(option => option.value === value)?.label ?? value;
}
