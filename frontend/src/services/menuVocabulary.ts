const normalizeCompact = (value?: string | null) =>
  (value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s　]+/g, "")
    .replace(/[＿_]/g, "")
    .replace(/[／/・+＋-]/g, "")
    .replace(/[()（）\[\]【】]/g, "");

export const DIET_TYPE_OPTIONS = [
  { value: "", label: "未選択" },
  { value: "regular", label: "常食" },
  { value: "regular_bag", label: "常食(袋分け)" },
  { value: "soft", label: "軟菜" },
  { value: "soft_mixer", label: "軟菜/ミキサー" },
  { value: "mixer", label: "ミキサー" },
  { value: "daycare", label: "通所" },
  { value: "staff", label: "職員" },
  { value: "tea", label: "お茶" },
  { value: "business", label: "事業" },
  { value: "diabetes", label: "糖尿" },
  { value: "pregnancy", label: "妊娠" },
  { value: "sesame_allergy", label: "ゴマアレルギー" },
  { value: "no_fried", label: "禁食(揚げ物禁)" },
  { value: "no_meat", label: "禁食(肉禁)" },
  { value: "forbidden_other", label: "禁食(肉卵魚禁)" },
  { value: "no_fish", label: "禁食(魚禁)" },
  { value: "change_1", label: "変更1" },
  { value: "change_2", label: "変更2" },
  { value: "regular_1600kcal", label: "常食1600kcal" },
  { value: "soft_1600kcal", label: "軟菜1600kcal" },
  { value: "mixer_1600kcal", label: "ミキサー1600kcal" },
  { value: "1600kcal", label: "1600kcal" },
];

const DIET_TYPE_LABELS = Object.fromEntries(
  DIET_TYPE_OPTIONS.filter((option) => option.value).map((option) => [option.value, option.label])
) as Record<string, string>;

export const normalizeDietTypeValue = (value?: string | null) => {
  const raw = (value || "").trim();
  if (!raw) return "";
  const compact = normalizeCompact(raw);
  if (!compact) return "";
  if ((compact.includes("袋") || compact.includes("bag")) && (compact.includes("regular") || compact.includes("常食") || compact.includes("通常") || compact === "常")) {
    return "regular_bag";
  }
  if (compact.includes("regular") || compact.includes("常食") || compact.includes("通常")) return "regular";
  if (compact.includes("daycare") || compact.includes("通所")) return "daycare";
  if (compact.includes("staff") || compact.includes("職員")) return "staff";
  if (compact.includes("tea") || compact.includes("お茶")) return "tea";
  if (compact.includes("business") || compact.includes("事業")) return "business";
  if (compact.includes("diabetes") || compact.includes("diabetic") || compact.includes("糖尿")) return "diabetes";
  if (compact.includes("pregnancy") || compact.includes("妊娠")) return "pregnancy";
  if ((compact.includes("ごま") || compact.includes("sesame")) && (raw.includes("アレル") || compact.includes("allergy"))) {
    return "sesame_allergy";
  }
  if (compact.includes("揚げ物禁") || compact.includes("揚物禁") || compact.includes("nofried") || compact.includes("friedfree")) return "no_fried";
  if (
    (compact.includes("肉") || compact.includes("meat")) &&
    (compact.includes("卵") || compact.includes("玉子") || compact.includes("egg")) &&
    (compact.includes("魚") || compact.includes("鯖") || compact.includes("さば") || compact.includes("fish"))
  ) {
    return "forbidden_other";
  }
  if (compact.includes("nomeat") || compact.includes("nobeef") || compact.includes("禁食肉禁") || compact.includes("肉禁")) return "no_meat";
  if (compact.includes("nofish") || compact.includes("禁食魚禁") || compact.includes("魚禁")) return "no_fish";
  if (compact.includes("change1") || compact.includes("変更1")) return "change_1";
  if (compact.includes("change2") || compact.includes("変更2")) return "change_2";
  const hasSoft = compact.includes("soft") || compact.includes("軟");
  const hasMixer = compact.includes("mixer") || compact.includes("mix") || compact.includes("ミキサ");
  if (hasSoft && hasMixer) return "soft_mixer";
  if (hasSoft) return "soft";
  if (hasMixer) return "mixer";
  if (compact.includes("1600")) {
    if (compact.includes("常食") || compact.includes("regular")) return "regular_1600kcal";
    if (compact.includes("軟") || compact.includes("soft")) return "soft_1600kcal";
    if (compact.includes("ミキサ") || compact.includes("mixer")) return "mixer_1600kcal";
    return "1600kcal";
  }
  return raw;
};

export const formatDietTypeLabel = (value?: string | null) => {
  const normalized = normalizeDietTypeValue(value);
  if (!normalized) return "-";
  return DIET_TYPE_LABELS[normalized] || value || "-";
};
