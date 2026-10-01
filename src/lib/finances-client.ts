import { amountFor, inMonth, money, monthLabel, summarize, type Category, type CategoryFilter, type ExpenseDay } from './finances';

export function initializeFinances() {
  const candidate = document.querySelector<HTMLElement>('[data-finances]');
  if (!candidate) return;
  const root: HTMLElement = candidate;
  const data: { records: ExpenseDay[]; categories: { key: Category; label: string }[] } = JSON.parse(root.querySelector('[data-finance-data]')!.textContent!);
  const month = root.querySelector<HTMLSelectElement>('[data-month]')!;
  const category = root.querySelector<HTMLSelectElement>('[data-category]')!;
  const search = root.querySelector<HTMLInputElement>('[data-search]')!;
  const sort = root.querySelector<HTMLSelectElement>('[data-sort]')!;
  const tbody = root.querySelector<HTMLTableSectionElement>('[data-ledger]')!;
  const chart = root.querySelector<HTMLElement>('[data-chart]')!;
  let visibleRows: ExpenseDay[] = [];

  const set = (selector: string, text: string) => { root.querySelector<HTMLElement>(selector)!.textContent = text; };

  function renderLedger() {
    const selected = category.value as CategoryFilter;
    const query = search.value.trim().toLocaleLowerCase();
    visibleRows = inMonth(data.records, month.value)
      .filter(row => selected === 'all' || row[selected] !== null)
      .filter(row => `${row.date} ${row.note}`.toLocaleLowerCase().includes(query))
      .sort((a, b) => {
        if (sort.value === 'amount-desc') return amountFor(b, selected) - amountFor(a, selected) || b.date.localeCompare(a.date);
        if (sort.value === 'amount-asc') return amountFor(a, selected) - amountFor(b, selected) || a.date.localeCompare(b.date);
        return sort.value === 'date-asc' ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date);
      });
    const fragment = document.createDocumentFragment();
    visibleRows.forEach(row => {
      const tr = document.createElement('tr');
      const values = [row.date, ...data.categories.map(item => row[item.key] === null ? '—' : money(row[item.key]!)), money(row.total), row.note || '—'];
      values.forEach((value, i) => {
        const cell = document.createElement(i === 0 ? 'th' : 'td');
        if (i === 0) cell.setAttribute('scope', 'row');
        if (i === 4) cell.className = 'finance-total-cell';
        if (i === 5) cell.className = 'finance-note-cell';
        if (i > 0 && i < 4 && data.categories[i - 1].key === selected) cell.classList.add('finance-selected-cell');
        cell.textContent = value;
        tr.append(cell);
      });
      fragment.append(tr);
    });
    tbody.replaceChildren(fragment);
    root.querySelector<HTMLElement>('[data-empty]')!.hidden = visibleRows.length > 0;
    set('[data-result-count]', `共 ${visibleRows.length} 天。类别筛选保留该类别有填写金额的日期；搜索仅筛选明细，不改变上方分析。`);
  }

  function renderDashboard() {
    const selected = category.value as CategoryFilter;
    const rows = inMonth(data.records, month.value);
    const summary = summarize(rows, selected);
    const categoryLabel = selected === 'all' ? '全部类别' : data.categories.find(item => item.key === selected)!.label;
    set('[data-period]', `${month.value === 'all' ? '全部月份' : monthLabel(month.value)} · ${categoryLabel} · 已记录 ${rows.length} 天`);
    set('[data-total]', money(summary.total));
    set('[data-average]', money(summary.average));
    set('[data-median]', money(summary.median));
    set('[data-peak]', money(summary.peak ? amountFor(summary.peak, selected) : 0));
    set('[data-peak-date]', summary.peak ? `${summary.peak.date}${summary.peak.note ? ` · ${summary.peak.note}` : ''}` : '暂无记录');

    const total = summarize(rows).total;
    data.categories.forEach(item => {
      const element = root.querySelector<HTMLElement>(`[data-composition="${item.key}"]`)!;
      const amount = summary.categories[item.key];
      const percent = total ? amount / total * 100 : 0;
      element.querySelector('[data-category-amount]')!.textContent = money(amount);
      element.querySelector('[data-category-share]')!.textContent = `${percent.toFixed(1)}%`;
      element.querySelector<HTMLElement>('.finance-fill')!.style.width = `${percent}%`;
    });
    const largest = [...data.categories].sort((a, b) => summary.categories[b.key] - summary.categories[a.key])[0];
    set('[data-insight]', total ? `${largest.label}占所选月份总支出的 ${(summary.categories[largest.key] / total * 100).toFixed(1)}%，是最大的支出类别。` : '所选月份暂无支出。');
    set('[data-peak-insight]', summary.peak && summary.total ? `${categoryLabel}支出最高的一天是 ${summary.peak.date}，共 ${money(amountFor(summary.peak, selected))}。` : '当前类别暂无支出。');

    const max = Math.max(...rows.map(row => amountFor(row, selected)), 1);
    const fragment = document.createDocumentFragment();
    rows.forEach(row => {
      const amount = amountFor(row, selected);
      const day = document.createElement('div');
      day.className = 'finance-day';
      day.title = `${row.date}：${selected !== 'all' && row[selected] === null ? '未填写' : money(amount)}${row.note ? ` · ${row.note}` : ''}`;
      const bar = document.createElement('div');
      bar.className = 'finance-day-bar';
      const fill = document.createElement('span');
      fill.style.height = `${amount / max * 100}%`;
      bar.append(fill);
      const label = document.createElement('span');
      label.textContent = row.date.slice(5);
      const accessible = document.createElement('span');
      accessible.className = 'sr-only';
      accessible.textContent = day.title;
      day.append(bar, label, accessible);
      fragment.append(day);
    });
    chart.replaceChildren(fragment);
    renderLedger();
  }

  month.addEventListener('change', renderDashboard);
  category.addEventListener('change', renderDashboard);
  search.addEventListener('input', renderLedger);
  sort.addEventListener('change', renderLedger);
  root.querySelector('[data-reset]')!.addEventListener('click', () => {
    month.value = category.value = 'all'; search.value = ''; sort.value = 'date-desc'; renderDashboard();
  });
  root.querySelectorAll<HTMLButtonElement>('[data-pick-month]').forEach(button => button.addEventListener('click', () => {
    month.value = button.dataset.pickMonth!; renderDashboard();
  }));
  root.querySelector('[data-export]')!.addEventListener('click', () => {
    const escape = (text: string) => `"${(/^[=+@\-\t\r\n]/.test(text) ? "'" + text : text).replace(/"/g, '""')}"`;
    const lines = [['日期', ...data.categories.map(item => item.label), '当天总计', '备注'], ...visibleRows.map(row => [row.date, ...data.categories.map(item => row[item.key] === null ? '' : (row[item.key]! / 100).toFixed(2)), (row.total / 100).toFixed(2), row.note])];
    const url = URL.createObjectURL(new Blob(['\uFEFF' + lines.map(line => line.map(escape).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url; link.download = `expenses-${month.value}-${category.value}.csv`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  renderDashboard();
}
