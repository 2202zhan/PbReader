import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { haptic } from './telegram';
import StepOptions from './StepOptions';
import StepPages from './StepPages';
import StepReview from './StepReview';
import PrinterPicker from './PrinterPicker';
import { money } from './format';

const STEPS = ['Настройки печати', 'Страницы', 'Проверка'];

export default function OrderWizard({ order: initial, onClose, onConfirmed }) {
  const [order, setOrder] = useState(initial);
  const [step, setStep] = useState(0);
  const [printers, setPrinters] = useState([]);
  const [sheet, setSheet] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Версия растёт на каждый пересчёт: по ней предпросмотр понимает, что лист
  // надо перерисовать, хотя номер его не менялся.
  const [version, setVersion] = useState(0);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    api.printers().then((data) => setPrinters(data.printers)).catch(() => {});
  }, []);

  const printer = printers.find((item) => item.id === order.printer_id);

  const patch = useCallback(
    async (changes, extra = {}) => {
      setBusy(true);
      setError('');
      try {
        const updated = await api.updateOrder(order.id, {
          options: changes,
          ...(extra.printerId ? { printer_id: extra.printerId } : {}),
        });
        setOrder(updated);
        setVersion((value) => value + 1);
        setSheet((current) => Math.min(current, updated.plan?.sheet_count || 1));
      } catch (exc) {
        setError(exc.message);
      } finally {
        setBusy(false);
      }
    },
    [order.id],
  );

  async function forward() {
    haptic();
    if (step < STEPS.length - 1) {
      setStep(step + 1);
      return;
    }
    setBusy(true);
    setError('');
    try {
      onConfirmed(await api.confirmOrder(order.id));
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  async function back() {
    if (step) {
      setStep(step - 1);
      return;
    }
    // Ушли с первого шага — заказа не случилось. Черновик удаляется, чтобы не
    // оседать в истории записью о том, чего не было. Не получилось удалить —
    // не беда, экран всё равно закрываем: это уборка, а не действие человека.
    try {
      await api.cancelOrder(order.id);
    } catch {
      /* пусто */
    }
    onClose();
  }

  const last = step === STEPS.length - 1;

  return (
    <>
      <div className="wizard-head">
        <button type="button" className="back" onClick={back}>‹</button>
        <h1>{STEPS[step]}</h1>
      </div>

      <div className="steps">
        {STEPS.map((title, index) => (
          <i key={title} className={index <= step ? 'passed' : ''} />
        ))}
      </div>

      {error && <div className="notice error">{error}</div>}

      {step === 0 && (
        <StepOptions order={order} printer={printer} printers={printers}
                     busy={busy} patch={patch} version={version}
                     onChangePlace={() => setPicking(true)} />
      )}

      {picking && (
        <PrinterPicker
          printers={printers}
          current={order.printer_id}
          onPick={(id) => patch({}, { printerId: id })}
          onClose={() => setPicking(false)}
        />
      )}
      {step === 1 && <StepPages order={order} busy={busy} patch={patch} />}
      {step === 2 && (
        <StepReview order={order} printer={printer} sheet={sheet}
                    setSheet={setSheet} version={version} />
      )}

      <div className="order-actions">
        <button type="button" className="primary" disabled={busy || !order.amount}
                onClick={forward}>
          {busy
            ? 'Считаю…'
            : `${last ? 'К оплате' : 'Далее'} · ${money(order.amount, order.currency)}`}
        </button>
      </div>
    </>
  );
}
