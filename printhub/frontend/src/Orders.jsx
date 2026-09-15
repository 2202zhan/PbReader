import { money, shortDate } from './format';
import { DocumentIcon } from './icons';

const STATES = {
  draft: { title: 'Черновик', tone: 'muted' },
  awaiting_payment: { title: 'Ждёт оплаты', tone: 'warn' },
  paid: { title: 'Оплачен', tone: 'ok' },
  printing: { title: 'Печатается', tone: 'ok' },
  done: { title: 'Готов', tone: 'ok' },
  failed: { title: 'Не удалось', tone: 'bad' },
  cancelled: { title: 'Отменён', tone: 'muted' },
};

export default function Orders({ orders, onOpen }) {
  if (!orders.length) {
    return (
      <div className="empty">
        <span className="glyph"><DocumentIcon /></span>
        Заказов пока нет
      </div>
    );
  }

  return (
    <div>
      {orders.map((order) => {
        const state = STATES[order.state] || { title: order.state, tone: 'muted' };
        return (
          <button type="button" className="file as-button" key={order.id}
                  onClick={() => onOpen(order)}>
            <div className="body">
              <div className="name">{order.file_name || 'Документ'}</div>
              <div className="meta">
                <span className={`tag ${state.tone}`}>{state.title}</span>
                {' · '}
                {shortDate(order.created_at)}
              </div>
            </div>
            <div className="amount">{money(order.amount, order.currency)}</div>
          </button>
        );
      })}
    </div>
  );
}
