import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { haptic, openLink } from './telegram';
import { money } from './format';

// Как часто спрашиваем, оплачено ли. Сервер при каждом таком запросе заодно
// спрашивает Kaspi — подтверждение приходит вебхуком, а вебхук может не дойти.
const POLL_MS = 3000;

export default function PayScreen({ order, onPaid, onClose }) {
  const [payment, setPayment] = useState(null);
  const [state, setState] = useState(order.state);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const poll = useCallback(async () => {
    try {
      const result = await api.paymentState(order.id);
      if (result.payment) setPayment(result.payment);
      setState(result.order_state);
      if (result.order_state === 'paid') {
        haptic('heavy');
        onPaid?.(result);
      }
    } catch (exc) {
      setError(exc.message);
    }
  }, [order.id, onPaid]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setBusy(true);
      try {
        const created = await api.pay(order.id);
        if (alive) setPayment(created);
      } catch (exc) {
        if (alive) setError(exc.message);
      } finally {
        if (alive) setBusy(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [order.id]);

  useEffect(() => {
    if (state === 'paid') return undefined;
    timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [poll, state]);

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

  if (state === 'paid') {
    return (
      <>
        <div className="done">
          <div className="done-mark">✓</div>
          <h1>Оплачено</h1>
          <p className="hint">
            {money(order.amount, order.currency)} — заказ принят.
          </p>
          <p className="hint small">
            Печать начнётся, когда вы подойдёте к принтеру: лист не должен лежать
            в лотке без вас.
          </p>
          {payment?.receipt_url && (
            <button type="button" className="link-button"
                    onClick={() => openLink(payment.receipt_url)}>
              Чек Kaspi
            </button>
          )}
        </div>
        <div className="order-actions">
          <button type="button" className="primary" onClick={onClose}>Готово</button>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="topbar">
        <button type="button" className="back" onClick={onClose}>← Назад</button>
        <span className="topbar-title">Оплата</span>
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="pay-amount">
        <span>К оплате</span>
        <strong>{money(order.amount, order.currency)}</strong>
      </div>

      <p className="hint">
        {order.file_name}
        {order.plan?.sheet_count ? ` · ${order.plan.sheet_count} л.` : ''}
      </p>

      {simulated ? (
        <>
          <div className="notice warn">
            Режим разработки: настоящая оплата не выставляется. Ниже — кнопки
            вместо приложения Kaspi.
          </div>
          <button type="button" className="primary" disabled={busy}
                  onClick={() => simulate('paid')}>
            Как будто оплатил
          </button>
          <button type="button" className="secondary" disabled={busy}
                  onClick={() => simulate('failed')}>
            Как будто не прошло
          </button>
        </>
      ) : (
        <div className="order-actions">
          <button
            type="button"
            className="primary"
            disabled={busy || !payment?.pay_url}
            onClick={() => {
              haptic('medium');
              openLink(payment.pay_url);
            }}
          >
            Оплатить через Kaspi
          </button>
          <p className="hint small center">
            Откроется приложение Kaspi. Вернитесь сюда — мы сами увидим оплату.
          </p>
        </div>
      )}
    </>
  );
}
