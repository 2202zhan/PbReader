import SheetPreview from './SheetPreview';
import { money, pagesWord, sheetsWord } from './format';

/**
 * Шаг 1 — то, что относится ко всему заказу.
 *
 * Недоступный вариант не прячется, а показывается погашенным и с причиной.
 * Спрятать проще, но тогда человек не понимает, есть ли вообще цветная печать,
 * и ищет её в других местах; а узнав, что на этой точке её нет, может выбрать
 * другую.
 */
export default function StepOptions({ order, printer, printers, busy, patch, version, onChangePlace }) {
  const options = order.options || {};
  const plan = order.plan || {};
  const color = options.color === 'color';
  const duplex = (options.duplex || 'simplex') !== 'simplex';
  const perSide = color ? printer?.price_color : printer?.price_mono;

  return (
    <>
      <div className="place">
        <i className="dot" />
        <span className="name">
          {printer ? printer.title : 'Точка не выбрана'}
          {printer?.location ? ` · ${printer.location}` : ''}
        </span>
        {printers.length > 1 && (
          <button type="button" onClick={onChangePlace}>Сменить</button>
        )}
      </div>

      <SheetPreview orderId={order.id} sheet={1} side="front" version={version} />

      {plan.paper?.margins_measured === false && (
        <p className="hint small">
          Поля показаны типовыми — настоящие знает драйвер принтера, а он ответит,
          когда аппарат подключат.
        </p>
      )}

      <div className="group-label">
        <span>Весь заказ</span>
        <span>шаг 1 из 3</span>
      </div>

      <div className="choices">
        <button type="button" className={`choice${!color ? ' on' : ''}`} disabled={busy}
                onClick={() => patch({ color: 'monochrome' })}>
          <i className="mark" />
          <span>
            <span className="title">Чёрно-белая</span>
            <span className="sub">{money(printer?.price_mono ?? 0)} за страницу</span>
          </span>
        </button>

        <button
          type="button"
          className={`choice${color ? ' on' : ''}`}
          disabled={busy || !printer?.color_supported}
          onClick={() => patch({ color: 'color' })}
        >
          <i className="mark" />
          <span>
            <span className="title">Цветная</span>
            <span className="sub">
              {printer?.color_supported
                ? `${money(printer?.price_color ?? 0)} за страницу`
                : 'на этой точке нет'}
            </span>
          </span>
        </button>
      </div>

      <div className="choices">
        <button type="button" className={`choice${!duplex ? ' on' : ''}`} disabled={busy}
                onClick={() => patch({ duplex: 'simplex' })}>
          <i className="mark" />
          <span>
            <span className="title">Одна сторона</span>
            <span className="sub">обычная печать</span>
          </span>
        </button>

        <button
          type="button"
          className={`choice${duplex ? ' on' : ''}`}
          disabled={busy || !printer?.duplex_supported}
          onClick={() => patch({ duplex: 'long-edge' })}
        >
          <i className="mark" />
          <span>
            <span className="title">Две стороны</span>
            <span className={`sub${printer?.duplex_discount ? ' plus' : ''}`}>
              {!printer?.duplex_supported
                ? 'на этой точке нет'
                : printer.duplex_discount
                  ? `−${printer.duplex_discount} % и вдвое меньше бумаги`
                  : 'вдвое меньше бумаги'}
            </span>
          </span>
        </button>
      </div>

      {perSide > 0 && plan.printed_sides > 0 && (
        <p className="hint small">
          {pagesWord(plan.printed_sides)} × {money(perSide)}
          {plan.sheet_count ? ` · ${sheetsWord(plan.sheet_count)} бумаги` : ''}
        </p>
      )}
    </>
  );
}
