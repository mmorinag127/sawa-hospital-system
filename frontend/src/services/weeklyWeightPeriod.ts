export type WeeklyWeightPeriod = { start: string; end: string };

const dayMs = 86400000;
const dateOnly = (value: string) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return null;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date : null;
};
const text = (date: Date) => date.toISOString().slice(0, 10);
const shift = (date: Date, days: number) => new Date(date.getTime() + days * dayMs);

export const businessPeriodForDate = (value: string): WeeklyWeightPeriod | null => {
  const date = dateOnly(value);
  if (!date) return null;
  const sunday = shift(date, -date.getUTCDay());
  const end = text(shift(sunday, 6));
  return dateOnly(text(sunday)) && dateOnly(end) ? { start: text(sunday), end } : null;
};

export const storedWeeklyWeightPeriod = (value: string): WeeklyWeightPeriod | null => {
  const match = /^(\d{4}-\d{2})@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/.exec(value);
  if (!match || match[1] !== match[2].slice(0, 7)) return null;
  const [, , start, end] = match;
  const business = businessPeriodForDate(start);
  if (!business || !dateOnly(end) || end < start || end > business.end) return null;
  return { start, end };
};

export const weekInputForPeriod = (period: WeeklyWeightPeriod): string => {
  const business = businessPeriodForDate(period.start);
  if (!business) return "";
  const monday = shift(dateOnly(business.start)!, 1);
  const thursday = shift(monday, 3);
  const year = thursday.getUTCFullYear();
  const janFourth = dateOnly(`${String(year).padStart(4, "0")}-01-04`);
  if (!janFourth) return "";
  const firstMonday = shift(janFourth, -((janFourth.getUTCDay() + 6) % 7));
  return `${String(year).padStart(4, "0")}-W${String(1 + Math.round((monday.getTime() - firstMonday.getTime()) / (7 * dayMs))).padStart(2, "0")}`;
};

export const weeklyWeightPeriod = (selection: string): WeeklyWeightPeriod | null => {
  if (selection.includes("@")) return storedWeeklyWeightPeriod(selection);
  const match = /^(\d{4})-W(\d{2})$/.exec(selection);
  if (!match) return null;
  const janFourth = dateOnly(`${match[1]}-01-04`);
  const week = Number(match[2]);
  if (!janFourth || week < 1 || week > 53) return null;
  const monday = shift(janFourth, -((janFourth.getUTCDay() + 6) % 7) + (week - 1) * 7);
  const period = businessPeriodForDate(text(shift(monday, -1)));
  return period && weekInputForPeriod(period) === selection ? period : null;
};

export const weeklyWeightDates = (period: WeeklyWeightPeriod): string[] => {
  const start = dateOnly(period.start);
  const end = dateOnly(period.end);
  const business = businessPeriodForDate(period.start);
  if (!start || !end || !business || period.end < period.start || period.end > business.end) return [];
  return Array.from({ length: 1 + (end.getTime() - start.getTime()) / dayMs }, (_, index) => text(shift(start, index)));
};

export const weeklyWeightConditionsPeriod = (conditions: { week: string; savedWeek: string }) => {
  if (!conditions.savedWeek) return weeklyWeightPeriod(conditions.week);
  const period = storedWeeklyWeightPeriod(conditions.savedWeek);
  return period && weekInputForPeriod(period) === conditions.week ? period : null;
};

export const currentBusinessWeek = () => {
  const now = new Date();
  const today = `${String(now.getFullYear()).padStart(4, "0")}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const period = businessPeriodForDate(today);
  return period ? weekInputForPeriod(period) : "";
};
