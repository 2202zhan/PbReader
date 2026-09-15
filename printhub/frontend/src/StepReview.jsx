import SheetPreview from './SheetPreview';
import { money, pagesWord, sheetsWord } from './format';

/**
 * Шаг 3 — что получится и сколько это стоит.
 *
 * Чек показывается строками, включая скидку: «итого» без расшифровки заставляет
 * человека верить на слово, а он расплачивается настоящими деньгами.
 */
export default function StepReview({ order, printer, sheet, setSheet, version }) {
  const plan = order.plan || {};
  const options = order.options || {};
  const sheetCount = plan.sheet_count || 1;
  const duplex = (options.duplex || 'simplex') !== 'simplex';

  return (
    <>
      <div className="group-label">
        <span>Проверьте</span>
        <span>шаг 3 из 3</span>
      </div>

      <SheetPreview orderId={order.id} sheet={sheet} side="front" version={version} />

      <div className="sheet-nav">
        <button type="button" disabled={sheet <= 1} onClick={() => setSheet(sheet - 1)}>‹</button>
        <span>Лист {sheet} из {sheetCount}</span>
        <button type="button" disabled={sheet >= sheetCount}
                onClick={() => setSheet(sheet + 1)}>›</button>
      </div>

      <div className="group-label">
        <span>{order.file_name}</span>
        <span>
          {options.color === 'color' ? 'цветная' : 'ч/б'} · {duplex ? '2 стороны' : '1 сторона'}
          {plan.paper?.name ? ` · ${plan.paper.name}` : ''}
        </span>
      </div>

      <div className="receipt">
        {(order.price?.lines || []).map((line) => (
          <div className={`line${line.amount < 0 ? ' off' : ''}`} key={line.title}>
            <span className="what">
              <b>{line.title}</b>
              {line.count > 1 && <span>{line.count} × {money(line.unit_price, order.currency)}</span>}
            </span>
            <span className="sum">{money(line.amount, order.currency)}</span>
          </div>
        ))}
        <div className="line total">
          <span className="what"><b>Итого</b></span>
          <span className="sum">{money(order.amount, order.currency)}</span>
        </div>
      </div>

      <p className="hint small">
        {sheetsWord(sheetCount)} бумаги · {pagesWord(plan.printed_sides || 0)} с печатью
        {plan.ink?.maximum_percent ? ` · заполнение до ${Math.round(plan.ink.maximum_percent)} %` : ''}
      </p>

      <div className="callout calm">
        Печать начнётся не сразу. Оплатите сейчас, а у аппарата
        {printer?.title ? ` «${printer.title}»` : ''} нажмёте «Я на месте» — лист не должен
        лежать в лотке без вас.
      </div>
    </>
  );
}
