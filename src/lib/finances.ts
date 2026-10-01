export const categoryKeys = ['food', 'transport', 'other'] as const;
export type Category = typeof categoryKeys[number];
export type CategoryFilter = Category | 'all';

export interface ExpenseDay {
  date: string;
  food: number | null;
  transport: number | null;
  other: number | null;
  total: number;
  note: string;
}

export const money = (fen: number) => new Intl.NumberFormat('zh-CN', {
  style: 'currency', currency: 'CNY', minimumFractionDigits: 2
}).format(fen / 100);

export const monthLabel = (month: string) => `${month.slice(0, 4)} 年 ${Number(month.slice(5))} 月`;
export const amountFor = (row: ExpenseDay, category: CategoryFilter) => category === 'all' ? row.total : row[category] ?? 0;
export const inMonth = (records: ExpenseDay[], month: string) => records.filter(row => month === 'all' || row.date.startsWith(month));

export function summarize(records: ExpenseDay[], category: CategoryFilter = 'all') {
  const amounts = records.map(row => amountFor(row, category)).sort((a, b) => a - b);
  const total = amounts.reduce((sum, value) => sum + value, 0);
  const middle = Math.floor(amounts.length / 2);
  const median = amounts.length ? amounts.length % 2 ? amounts[middle] : (amounts[middle - 1] + amounts[middle]) / 2 : 0;
  const peak = records.reduce<ExpenseDay | undefined>((best, row) => !best || amountFor(row, category) > amountFor(best, category) ? row : best, undefined);
  const categories = Object.fromEntries(categoryKeys.map(key => [key, records.reduce((sum, row) => sum + (row[key] ?? 0), 0)])) as Record<Category, number>;
  return { total, average: records.length ? total / records.length : 0, median, peak, categories, days: records.length };
}

export function monthCoverage(records: ExpenseDay[], month: string) {
  const rows = inMonth(records, month);
  const [year, value] = month.split('-').map(Number);
  const days = new Date(Date.UTC(year, value, 0)).getUTCDate();
  return { days, recorded: rows.length, complete: rows.length === days };
}
