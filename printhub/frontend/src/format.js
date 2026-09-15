export function humanSize(bytes) {
  if (!bytes) return '';
  const units = ['Б', 'КБ', 'МБ', 'ГБ'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

// «1 страница», «2 страницы», «5 страниц» — по-русски это три разные формы,
// и «5 страница» в интерфейсе выглядит как недоделка.
export function pagesWord(count) {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} страниц`;
  if (ones === 1) return `${count} страница`;
  if (ones >= 2 && ones <= 4) return `${count} страницы`;
  return `${count} страниц`;
}

export function filesWord(count) {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} файлов`;
  if (ones === 1) return `${count} файл`;
  if (ones >= 2 && ones <= 4) return `${count} файла`;
  return `${count} файлов`;
}

export function shortDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('ru', { day: 'numeric', month: 'short' });
}

export function sheetsWord(count) {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} листов`;
  if (ones === 1) return `${count} лист`;
  if (ones >= 2 && ones <= 4) return `${count} листа`;
  return `${count} листов`;
}

// Тенге целые — дробей в обороте нет, и сервер считает в целых.
export function money(amount, currency = 'KZT') {
  const value = new Intl.NumberFormat('ru-RU').format(amount || 0);
  return currency === 'KZT' ? `${value} ₸` : `${value} ${currency}`;
}
