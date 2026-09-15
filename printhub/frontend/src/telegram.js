// Обёртка над SDK телеграма.
//
// Веб-апп должен открываться и в обычном браузере — иначе разрабатывать можно
// только с телефона в руках. Поэтому ни одно обращение к Telegram.WebApp не
// делается напрямую: если SDK нет, приложение работает в «настольном» режиме.

const tg = typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined;

export const isTelegram = Boolean(tg?.initData);

export function ready() {
  if (!tg) return;
  tg.ready();
  tg.expand();
}

export function initData() {
  return tg?.initData || '';
}

export function themeParam(name, fallback) {
  const value = tg?.themeParams?.[name];
  return value || fallback;
}

export function haptic(style = 'light') {
  try {
    tg?.HapticFeedback?.impactOccurred(style);
  } catch {
    /* на настольном браузере вибрации нет — это не повод падать */
  }
}

export function openLink(url) {
  if (tg?.openLink) tg.openLink(url);
  else window.open(url, '_blank', 'noopener');
}
