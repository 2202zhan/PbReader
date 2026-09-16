import { DocumentIcon } from './icons';

/**
 * Файл не берём — и объясняем, почему именно этот.
 *
 * Полоска ошибки поверх списка тут не годится: человек только что выбрал файл
 * и ждёт, что сейчас начнётся заказ. Отказ должен занять весь экран, назвать
 * файл по имени, сказать, чем он на самом деле является, и дать понятный
 * следующий шаг. Иначе вывод будет один: «приложение сломалось».
 */
export default function Rejected({ reason, onRetry, onClose }) {
  return (
    <>
      <div className="done">
        <div className="done-mark warn"><DocumentIcon /></div>
        <h1>Так не получится</h1>
        <p className="hint">{reason}</p>
        <div className="callout calm">
          Сохраните документ в PDF и загрузите снова — из Word это «Файл →
          Сохранить как → PDF», с телефона проще всего «Печать → Сохранить в PDF».
        </div>
      </div>
      <div className="order-actions">
        <button type="button" className="primary" onClick={onRetry}>
          Выбрать другой файл
        </button>
        <button type="button" className="secondary" onClick={onClose}>Не сейчас</button>
      </div>
    </>
  );
}
