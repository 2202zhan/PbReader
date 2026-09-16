import { useMemo } from 'react';
import PageThumb from './PageThumb';

/**
 * Шаг 2 — что именно печатаем.
 *
 * Страницы отмечаются галочками по картинкам, а не строкой «1,3,5-8». Строку
 * быстрее набрать с клавиатуры, но на телефоне её набирают с ошибками и
 * проверить её нечем: человек узнаёт, что взял не те страницы, уже по бумаге.
 */
export default function StepPages({ order, busy, patch }) {
  const options = order.options || {};
  const plan = order.plan || {};
  const total = plan.document?.page_count || 1;
  const all = useMemo(() => Array.from({ length: total }, (_, i) => i + 1), [total]);

  // Пустая строка означает «все страницы» — так это понимает и PbReader.
  const selected = useMemo(() => {
    const raw = (options.pages || '').trim();
    if (!raw) return new Set(all);
    const chosen = new Set();
    raw.split(',').forEach((part) => {
      const [from, to] = part.split('-').map((n) => parseInt(n, 10));
      if (Number.isNaN(from)) return;
      for (let n = from; n <= (Number.isNaN(to) ? from : to); n += 1) chosen.add(n);
    });
    return chosen;
  }, [options.pages, all]);

  function apply(next) {
    // Всё выбрано — отправляем пустую строку: так заказ не развалится, если
    // человек потом заменит файл на более длинный.
    patch({ pages: next.size === total ? '' : all.filter((n) => next.has(n)).join(',') });
  }

  function toggle(page) {
    const next = new Set(selected);
    if (next.has(page)) next.delete(page);
    else next.add(page);
    if (next.size === 0) return;   // ноль страниц — это не заказ
    apply(next);
  }

  const landscape = (options.orientation || 'portrait') === 'landscape';
  const copies = options.copies || 1;

  return (
    <>
      <div className="group-label">
        <span>{total > 1 ? `Страницы · выбрано ${selected.size} из ${total}` : 'Документ'}</span>
        <span>шаг 2 из 3</span>
      </div>

      {/* В документе одна страница — выбирать не из чего. Сетка из одной
          карточки и кнопка «выбрать все» выглядели бы издевательством. */}
      {total > 1 ? (
        <>
          <div className="pages">
            {all.map((page) => (
              <PageThumb
                key={page}
                orderId={order.id}
                page={page}
                selected={selected.has(page)}
                disabled={busy}
                onToggle={() => toggle(page)}
              />
            ))}
          </div>

          <p className="hint small">
            <button type="button" className="text-button" disabled={busy}
                    onClick={() => apply(new Set(all))}>
              Выбрать все
            </button>
            {selected.size > 1 && ' · '}
            {selected.size > 1 && (
              <button type="button" className="text-button" disabled={busy}
                      onClick={() => apply(new Set([all[0]]))}>
                Оставить одну
              </button>
            )}
          </p>
        </>
      ) : (
        <p className="hint small">
          В документе одна страница — печатаем её.
        </p>
      )}

      <div className="field">
        <label>
          Разворот
          <em>{landscape ? 'сейчас альбомный' : 'сейчас книжный'}</em>
        </label>
        <button type="button" className="secondary compact" disabled={busy}
                onClick={() => patch({ orientation: landscape ? 'portrait' : 'landscape' })}>
          Повернуть
        </button>
      </div>

      <div className="field">
        <label>
          Копии
          <em>каждой выбранной страницы</em>
        </label>
        <div className="stepper">
          <button type="button" disabled={busy || copies <= 1}
                  onClick={() => patch({ copies: copies - 1 })}>−</button>
          <span>{copies}</span>
          <button type="button" disabled={busy || copies >= 99}
                  onClick={() => patch({ copies: copies + 1 })}>+</button>
        </div>
      </div>
    </>
  );
}
