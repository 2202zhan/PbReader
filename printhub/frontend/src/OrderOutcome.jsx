import { money, shortDate } from './format';

/**
 * Чем кончился заказ, если кончился плохо.
 *
 * Самый важный экран во всём приложении. Человек заплатил, бумага не вышла,
 * рядом никого. Здесь он либо получает ответ, либо уходит навсегда — поэтому
 * тут нет ни «произошла ошибка», ни предложения написать в поддержку: сказано,
 * что случилось, и что деньги уже возвращаются.
 */
export default function OrderOutcome({ order, onClose }) {
  const refunded = order.state === 'refunded';

  return (
    <>
      <div className="done">
        <div className={`done-mark ${refunded ? '' : 'bad'}`}>{refunded ? '↩' : '!'}</div>
        <h1>{refunded ? 'Деньги вернулись' : 'Напечатать не удалось'}</h1>
        <p className="hint">
          {order.file_name} · {shortDate(order.created_at)}
        </p>

        <div className="receipt">
          <div className="line total">
            <span className="what"><b>{refunded ? 'Вернули' : 'Оплачено'}</b></span>
            <span className="sum">{money(order.amount, order.currency)}</span>
          </div>
        </div>

        <div className="callout calm">
          {refunded
            ? 'Возврат ушёл в Kaspi. Обычно деньги видно сразу, иногда банк держит их до суток.'
            : 'Заказ остановлен, деньги возвращаются на карту Kaspi автоматически — ничего делать не нужно.'}
        </div>

        <p className="hint small">
          Заказ остался в истории вместе с параметрами: повторить его можно в
          пару касаний, когда аппарат починят.
        </p>
      </div>
      <div className="order-actions">
        <button type="button" className="primary" onClick={onClose}>Понятно</button>
      </div>
    </>
  );
}
