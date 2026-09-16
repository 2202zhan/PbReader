import { money } from './format';

/**
 * Выбор точки печати.
 *
 * Раньше «Сменить» перебирало точки по кругу. На двух это ещё работало, на
 * десяти — уже нет: человек не видит, куда попадёт, и не может сравнить.
 * А сравнивать есть что: цена и возможности у точек разные, и цветная печать
 * может быть только на одной из них.
 */
export default function PrinterPicker({ printers, current, onPick, onClose }) {
  return (
    <div className="sheet-backdrop" onClick={onClose} role="presentation">
      <div className="bottom-sheet" onClick={(event) => event.stopPropagation()} role="dialog">
        <div className="grabber" />
        <h2>Где печатаем</h2>

        {printers.length === 0 && (
          <p className="hint">Точек печати пока нет. Загляните позже.</p>
        )}

        {printers.map((printer) => (
          <button
            type="button"
            key={printer.id}
            className={`point${printer.id === current ? ' on' : ''}`}
            onClick={() => {
              onPick(printer.id);
              onClose();
            }}
          >
            <span className="body">
              <span className="title">{printer.title}</span>
              <span className="sub">{printer.location || 'адрес не указан'}</span>
              <span className="tags">
                <span>{money(printer.price_mono)}/стр</span>
                {printer.color_supported
                  ? <span className="good">цветная {money(printer.price_color)}</span>
                  : <span className="muted">только ч/б</span>}
                {printer.duplex_supported
                  ? <span>две стороны{printer.duplex_discount ? ` −${printer.duplex_discount} %` : ''}</span>
                  : <span className="muted">одна сторона</span>}
              </span>
            </span>
            <span className="mark" />
          </button>
        ))}

        <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
      </div>
    </div>
  );
}
