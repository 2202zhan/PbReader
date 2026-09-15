import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { haptic } from './telegram';
import StepOptions from './StepOptions';
import StepPages from './StepPages';
import StepReview from './StepReview';
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

  useEffect(() => {
    api.printers().then((data) => setPrinters(data.printers)).catch(() => {});
  }, []);

  const printer = printers.find((item) => item.id === order.printer_id);

  const patch = useCallback(
    async (changes, extra = {}) => {
      setBusy(true);
      setError('');
      try {
        let printerId;
        if (extra.nextPrinter && printers.length > 1) {
          const index = printers.findIndex((item) => item.id === order.printer_id);
          printerId = printers[(index + 1) % printers.length].id;
        }
        const updated = await api.updateOrder(order.id, {
          options: changes,
          ...(printerId ? { printer_id: printerId } : {}),
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
    [order.id, order.printer_id, printers],
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

  const last = step === STEPS.length - 1;

  return (
    <>
      <div className="wizard-head">
        <button type="button" className="back"
                onClick={() => (step ? setStep(step - 1) : onClose())}>‹</button>
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
                     busy={busy} patch={patch} version={version} />
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
