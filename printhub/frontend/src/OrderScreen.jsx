import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import SheetPreview from './SheetPreview';
import { haptic } from './telegram';
import { money, pagesWord, sheetsWord } from './format';

const SCALES = [
  { value: 'auto', title: 'Авто' },
  { value: 'fit', title: 'Вписать' },
  { value: 'actual', title: '1:1' },
];

export default function OrderScreen({ order: initial, onChange, onClose, onConfirmed }) {
  const [order, setOrder] = useState(initial);
  const [printers, setPrinters] = useState([]);
  const [sheet, setSheet] = useState(1);
  const [side, setSide] = useState('front');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Версия меняется на каждый пересчёт — по ней предпросмотр понимает, что
  // картинку надо перезапросить, хотя номер листа и сторона те же.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    api.printers().then((data) => setPrinters(data.printers)).catch(() => {});
  }, []);

  const options = order.options || {};
  const plan = order.plan || {};
  const sheetCount = plan.sheet_count || 1;
  const printer = printers.find((item) => item.id === order.printer_id);
  const duplex = (options.duplex || 'simplex') !== 'simplex';

  const patch = useCallback(
    async (changes, printerId) => {
      setBusy(true);
      setError('');
      try {
        const updated = await api.updateOrder(order.id, {
          options: changes,
          ...(printerId ? { printer_id: printerId } : {}),
        });
        setOrder(updated);
        onChange?.(updated);
        setVersion((value) => value + 1);
        const total = updated.plan?.sheet_count || 1;
        if (sheet > total) setSheet(total);
      } catch (exc) {
        setError(exc.message);
      } finally {
        setBusy(false);
      }
    },
    [order.id, onChange, sheet],
  );

  async function confirm() {
    haptic('medium');
    setBusy(true);
    setError('');
    try {
      const confirmed = await api.confirmOrder(order.id);
      onConfirmed?.(confirmed);
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  const measured = plan.paper?.margins_measured;

  return (
    <>
      <div className="topbar">
        <button type="button" className="back" onClick={onClose}>← Назад</button>
        <span className="topbar-title">{order.file_name}</span>
      </div>

      {error && <div className="notice error">{error}</div>}

      <SheetPreview orderId={order.id} sheet={sheet} side={side} version={version} />

      <div className="sheet-nav">
        <button type="button" disabled={sheet <= 1} onClick={() => setSheet((n) => n - 1)}>‹</button>
        <span>Лист {sheet} из {sheetCount}</span>
        <button
          type="button"
          disabled={sheet >= sheetCount}
          onClick={() => setSheet((n) => n + 1)}
        >›</button>
      </div>

      {duplex && (
        <div className="segmented">
          <button type="button" className={side === 'front' ? 'on' : ''} onClick={() => setSide('front')}>
            Лицо
          </button>
          <button type="button" className={side === 'back' ? 'on' : ''} onClick={() => setSide('back')}>
            Оборот
          </button>
        </div>
      )}

      {measured === false && (
        <p className="hint small">
          Поля показаны типовыми: настоящие знает драйвер принтера, а он ответит,
          когда аппарат подключат.
        </p>
      )}

      <h2>Параметры</h2>

      {printers.length > 1 && (
        <div className="field">
          <label htmlFor="printer">Где печатаем</label>
          <select
            id="printer"
            value={order.printer_id || ''}
            disabled={busy}
            onChange={(event) => patch({}, event.target.value)}
          >
            {printers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}{item.location ? ` — ${item.location}` : ''}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="field">
        <label htmlFor="copies">Копии</label>
        <div className="stepper">
          <button type="button" disabled={busy || (options.copies || 1) <= 1}
                  onClick={() => patch({ copies: (options.copies || 1) - 1 })}>−</button>
          <span>{options.copies || 1}</span>
          <button type="button" disabled={busy || (options.copies || 1) >= 99}
                  onClick={() => patch({ copies: (options.copies || 1) + 1 })}>+</button>
        </div>
      </div>

      <div className="field">
        <label htmlFor="pages">Страницы</label>
        <input
          id="pages"
          type="text"
          inputMode="numeric"
          placeholder="все"
          defaultValue={options.pages || ''}
          disabled={busy}
          onBlur={(event) => {
            if ((event.target.value || '') !== (options.pages || '')) {
              patch({ pages: event.target.value });
            }
          }}
        />
      </div>

      {printer?.color_supported && (
        <div className="field">
          <label>Цвет</label>
          <div className="segmented">
            <button type="button" disabled={busy}
                    className={options.color !== 'color' ? 'on' : ''}
                    onClick={() => patch({ color: 'monochrome' })}>Ч/б</button>
            <button type="button" disabled={busy}
                    className={options.color === 'color' ? 'on' : ''}
                    onClick={() => patch({ color: 'color' })}>Цветная</button>
          </div>
        </div>
      )}

      {printer?.duplex_supported && (
        <div className="field">
          <label>Стороны</label>
          <div className="segmented">
            <button type="button" disabled={busy} className={!duplex ? 'on' : ''}
                    onClick={() => { setSide('front'); patch({ duplex: 'simplex' }); }}>Одна</button>
            <button type="button" disabled={busy} className={duplex ? 'on' : ''}
                    onClick={() => patch({ duplex: 'long-edge' })}>Обе</button>
          </div>
        </div>
      )}

      <div className="field">
        <label>Масштаб</label>
        <div className="segmented">
          {SCALES.map((item) => (
            <button
              key={item.value}
              type="button"
              disabled={busy}
              className={(options.scale || 'auto') === item.value ? 'on' : ''}
              onClick={() => patch({ scale: item.value })}
            >
              {item.title}
            </button>
          ))}
        </div>
      </div>

      <h2>Стоимость</h2>
      <div className="rows">
        {(order.price?.lines || []).map((line) => (
          <div className="row" key={line.title}>
            <span>{line.title}{line.count > 1 ? ` × ${line.count}` : ''}</span>
            <span>{money(line.amount, order.currency)}</span>
          </div>
        ))}
        <div className="row total">
          <span>Итого</span>
          <span>{money(order.amount, order.currency)}</span>
        </div>
      </div>

      <p className="hint small">
        {sheetsWord(sheetCount)}
        {plan.printed_sides ? `, ${pagesWord(plan.printed_sides)} с печатью` : ''}
        {plan.ink?.maximum_percent
          ? `, заполнение до ${Math.round(plan.ink.maximum_percent)} %`
          : ''}
      </p>

      {/* Итог и кнопка закреплены внизу: предпросмотр занимает пол-экрана, и
          иначе до самого важного — сколько это стоит и как согласиться — на
          телефоне пришлось бы доскроллить. */}
      <div className="order-actions">
        <div className="order-total">
          <span>Итого</span>
          <strong>{money(order.amount, order.currency)}</strong>
        </div>
        <button type="button" className="primary" disabled={busy || !order.amount} onClick={confirm}>
          {busy ? 'Считаю…' : 'Заказать'}
        </button>
        <p className="hint small center">Дальше — оплата через Kaspi.</p>
      </div>
    </>
  );
}
