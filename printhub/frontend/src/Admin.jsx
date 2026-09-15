import { useEffect, useState } from 'react';
import { api } from './api';
import { money, shortDate } from './format';

const EMPTY_TARIFF = {
  price_mono: 30,
  price_color: 150,
  heavy_ink_from: 0.3,
  heavy_extra_mono: 30,
  heavy_extra_color: 150,
  min_order: 0,
};

export default function Admin() {
  const [printers, setPrinters] = useState([]);
  const [tariffs, setTariffs] = useState([]);
  const [orders, setOrders] = useState([]);
  const [scope, setScope] = useState('default');
  const [form, setForm] = useState(EMPTY_TARIFF);
  const [newPrinter, setNewPrinter] = useState({ id: '', title: '', location: '' });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function load() {
    const [printerData, tariffData, orderData] = await Promise.all([
      api.adminPrinters(),
      api.adminTariffs(),
      api.adminOrders(),
    ]);
    setPrinters(printerData.printers);
    setTariffs(tariffData.tariffs);
    setOrders(orderData.orders);
    return tariffData.tariffs;
  }

  useEffect(() => {
    load()
      .then((rows) => {
        const current = rows.find((row) => row.printer_id === null);
        if (current) setForm({ ...EMPTY_TARIFF, ...current });
      })
      .catch((exc) => setError(exc.message));
  }, []);

  function pickScope(value) {
    setScope(value);
    const row = tariffs.find((item) => (item.printer_id || 'default') === value);
    setForm({ ...EMPTY_TARIFF, ...(row || {}) });
    setMessage('');
  }

  async function saveTariff(event) {
    event.preventDefault();
    setError('');
    setMessage('');
    try {
      await api.adminSetTariff(scope, {
        price_mono: Number(form.price_mono),
        price_color: Number(form.price_color),
        heavy_ink_from: Number(form.heavy_ink_from),
        heavy_extra_mono: Number(form.heavy_extra_mono),
        heavy_extra_color: Number(form.heavy_extra_color),
        min_order: Number(form.min_order),
      });
      setTariffs(await load());
      setMessage('Прайс сохранён. Новые заказы считаются по нему; уже подтверждённые — нет.');
    } catch (exc) {
      setError(exc.message);
    }
  }

  async function addPrinter(event) {
    event.preventDefault();
    setError('');
    try {
      await api.adminCreatePrinter({ ...newPrinter, duplex_supported: true });
      setNewPrinter({ id: '', title: '', location: '' });
      await load();
    } catch (exc) {
      setError(exc.message);
    }
  }

  const field = (name, label, hint) => (
    <div className="field" key={name}>
      <label htmlFor={name}>
        {label}
        {hint && <em>{hint}</em>}
      </label>
      <input
        id={name}
        type="number"
        step={name === 'heavy_ink_from' ? '0.05' : '1'}
        value={form[name]}
        onChange={(event) => setForm({ ...form, [name]: event.target.value })}
      />
    </div>
  );

  return (
    <>
      <h1>Админка</h1>
      <p className="hint">Цены задаём здесь. В коде их нет.</p>

      {error && <div className="notice error">{error}</div>}
      {message && <div className="notice ok">{message}</div>}

      <h2>Прайс</h2>
      <div className="segmented wrap">
        <button type="button" className={scope === 'default' ? 'on' : ''}
                onClick={() => pickScope('default')}>Общий</button>
        {printers.map((printer) => (
          <button key={printer.id} type="button" className={scope === printer.id ? 'on' : ''}
                  onClick={() => pickScope(printer.id)}>
            {printer.title}
          </button>
        ))}
      </div>

      <form onSubmit={saveTariff} className="card">
        {field('price_mono', 'Ч/б, ₸ за сторону')}
        {field('price_color', 'Цветная, ₸ за сторону')}
        {field('heavy_ink_from', 'Порог заливки', 'доля, 0.3 = 30 %')}
        {field('heavy_extra_mono', 'Надбавка ч/б, ₸')}
        {field('heavy_extra_color', 'Надбавка цветная, ₸')}
        {field('min_order', 'Минимальный заказ, ₸')}
        <button type="submit" className="primary">Сохранить прайс</button>
      </form>

      <p className="hint small">
        Считается за запечатанную сторону, а не за лист: двусторонняя печать тратит
        вдвое больше тонера при той же бумаге. Надбавка берётся со страниц, залитых
        сильнее порога, — это защита от «распечатаю чёрный квадрат».
      </p>

      <h2>Точки печати</h2>
      {printers.map((printer) => (
        <div className="file" key={printer.id}>
          <div className="body">
            <div className="name">{printer.title}</div>
            <div className="meta">
              {printer.id} · {printer.location || 'без адреса'} ·{' '}
              {printer.is_active ? 'работает' : 'выключена'}
              {!printer.margins_measured && ' · поля не измерены'}
            </div>
          </div>
        </div>
      ))}

      <form onSubmit={addPrinter} className="card">
        <div className="field">
          <label htmlFor="pid">Код <em>попадёт в ссылку с QR</em></label>
          <input id="pid" value={newPrinter.id} required
                 onChange={(event) => setNewPrinter({ ...newPrinter, id: event.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="ptitle">Название</label>
          <input id="ptitle" value={newPrinter.title}
                 onChange={(event) => setNewPrinter({ ...newPrinter, title: event.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="ploc">Адрес</label>
          <input id="ploc" value={newPrinter.location}
                 onChange={(event) => setNewPrinter({ ...newPrinter, location: event.target.value })} />
        </div>
        <button type="submit" className="primary">Добавить точку</button>
      </form>

      <h2>Последние заказы</h2>
      {orders.length === 0 && <p className="hint">Заказов пока нет</p>}
      {orders.slice(0, 20).map((order) => (
        <div className="file" key={order.id}>
          <div className="body">
            <div className="name">{order.file_name || 'Документ'}</div>
            <div className="meta">
              №{order.user_id} · {order.state} · {shortDate(order.created_at)}
            </div>
          </div>
          <div className="amount">{money(order.amount, order.currency)}</div>
        </div>
      ))}
    </>
  );
}
