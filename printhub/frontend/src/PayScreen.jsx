import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { haptic, openLink } from './telegram';
import { money } from './format';

// Сколько ждём оплату, прежде чем предложить выставить счёт заново.
const WINDOW_SECONDS = 300;
// Как часто спрашиваем. Сервер при каждом запросе заодно спрашивает Kaspi —
// подтверждение приходит вебхуком, а вебхук может и не дойти.
const POLL_MS = 3000;

function Dial({ left }) {
  const ratio = Math.max(0, left / WINDOW_SECONDS);
  const radius = 42;
  const length = 2 * Math.PI * radius;
  const minutes = Math.floor(left / 60);
  const seconds = String(left % 60).padStart(2, '0');

  return (
    <div className="dial">
      <svg width="92" height="92" viewBox="0 0 92 92">
        <circle cx="46" cy="46" r={radius} fill="none" strokeWidth="4"
                stroke="color-mix(in srgb, currentColor 18%, transparent)" />
        <circle cx="46" cy="46" r={radius} fill="none" strokeWidth="4" strokeLinecap="round"
                stroke="var(--ink)" strokeDasharray={length}
                strokeDashoffset={length * (1 - ratio)} />
      </svg>
      <span>{minutes}:{seconds}</span>
    </div>
  );
}

export default function PayScreen({ order, onPaid, onClose }) {
  const [payment, setPayment] = useState(null);
  const [state, setState] = useState(order.state);
  const [left, setLeft] = useState(WINDOW_SECONDS);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const started = useRef(false);

  const poll = useCallback(async () => {
    const result = await api.paymentState(order.id);
    if (result.payment) setPayment(result.payment);
    setState(result.order_state);
    if (result.order_state === 'paid') {
      haptic('heavy');
      onPaid?.(result);
    }
    return result;
  }, [order.id, onPaid]);

  const issue = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      setPayment(await api.pay(order.id));
      setLeft(WINDOW_SECONDS);
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }, [order.id]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // Открыть оплаченный заказ можно и из списка — например, чтобы наконец
    // нажать «Я на месте». Выставлять счёт заново в этот момент не нужно:
    // сервер справедливо откажет, а человек увидит ошибку на экране, где всё
    // в порядке.
    if (order.state === 'paid' || order.state === 'queued') return;
    issue();
  }, [issue, order.state]);

  useEffect(() => {
    if (state === 'paid' || state === 'queued') return undefined;
    const poller = setInterval(() => poll().catch((exc) => setError(exc.message)), POLL_MS);
    const ticker = setInterval(() => setLeft((value) => Math.max(0, value - 1)), 1000);
    return () => {
      clearInterval(poller);
      clearInterval(ticker);
    };
  }, [poll, state]);

  async function check() {
    setChecking(true);
    setError('');
    try {
      await poll();
    } catch (exc) {
      setError(exc.message);
    } finally {
      setChecking(false);
    }
  }

  async function release() {
    haptic('medium');
    setBusy(true);
    setError('');
    try {
      const updated = await api.release(order.id);
      setState(updated.state);
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  // В разработке ссылка ведёт внутрь приложения, а не в Kaspi.
  const simulated = payment?.pay_url?.startsWith('/dev-pay/');
  const externalId = simulated ? payment.pay_url.split('/').pop() : null;

  async function simulate(result) {
    setBusy(true);
    try {
      await api.devPay(externalId, result);
      await poll();
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  if (state === 'queued') {
    return (
      <>
        <div className="done">
          <div className="done-mark">→</div>
          <h1>Отправлено на принтер</h1>
          <p className="hint">Заберите листы из лотка.</p>
          <p className="hint small">
            Если бумага не пошла, не уходите: деньги вернутся автоматически, а
            заказ останется в истории.
          </p>
        </div>
        <div className="order-actions">
          <button type="button" className="primary" onClick={onClose}>Готово</button>
        </div>
      </>
    );
  }

  if (state === 'paid') {
    return (
      <>
        <div className="done">
          <div className="done-mark ok">✓</div>
          <h1>Оплачено</h1>
          <p className="hint">{money(order.amount, order.currency)} — заказ ваш.</p>
          <div className="callout calm">
            Нажмите «Я на месте», когда подойдёте к аппарату. Лист выедет в
            общий лоток, и до тех пор ему лучше подождать у нас.
          </div>
          {payment?.receipt_url && (
            <button type="button" className="text-button"
                    onClick={() => openLink(payment.receipt_url)}>
              Чек Kaspi
            </button>
          )}
        </div>
        <div className="order-actions">
          <button type="button" className="primary" disabled={busy} onClick={release}>
            Я на месте, печатать
          </button>
          <button type="button" className="secondary" onClick={onClose}>Позже</button>
        </div>
      </>
    );
  }

  const expired = left === 0;

  return (
    <>
      <div className="wizard-head">
        <button type="button" className="back" onClick={onClose}>‹</button>
        <h1>Оплата</h1>
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="waiting">
        <Dial left={left} />
        <h1>{expired ? 'Счёт просрочен' : 'Ждём оплату'}</h1>
        <p className="hint">
          {expired
            ? 'Выставим новый — заказ и цена останутся прежними.'
            : 'Оплатите в Kaspi — увидим сами, ничего нажимать не нужно.'}
        </p>
      </div>

      <div className="how">
        <div><b>1</b>Открываете Kaspi по кнопке</div>
        <div><b>2</b>Платите {money(order.amount, order.currency)}</div>
        <div><b>3</b>Возвращаетесь сюда</div>
      </div>

      <div className="receipt">
        <div className="line total">
          <span className="what"><b>К оплате</b></span>
          <span className="sum">{money(order.amount, order.currency)}</span>
        </div>
      </div>

      {simulated && (
        <>
          <div className="callout">
            Режим разработки: настоящий счёт не выставляется. Ниже — кнопки
            вместо приложения Kaspi.
          </div>
          <button type="button" className="primary" disabled={busy}
                  onClick={() => simulate('paid')}>Как будто оплатил</button>
          <button type="button" className="secondary" disabled={busy}
                  onClick={() => simulate('failed')}>Как будто не прошло</button>
        </>
      )}

      <div className="order-actions">
        {expired ? (
          <button type="button" className="primary" disabled={busy} onClick={issue}>
            Выставить счёт заново
          </button>
        ) : (
          !simulated && (
            <button type="button" className="primary" disabled={busy || !payment?.pay_url}
                    onClick={() => { haptic('medium'); openLink(payment.pay_url); }}>
              Оплатить через Kaspi
            </button>
          )
        )}
        <button type="button" className="secondary" disabled={checking} onClick={check}>
          {checking ? 'Проверяю…' : 'Я оплатил, проверьте'}
        </button>
      </div>
    </>
  );
}
