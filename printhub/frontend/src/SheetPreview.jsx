import { useEffect, useRef, useState } from 'react';
import { api } from './api';

// Ширина, в которой сервер рисует лист. Берём с запасом под плотные экраны:
// на телефоне с DPR 3 картинка в 360 px выглядит мылом.
const RENDER_WIDTH = 900;

export default function SheetPreview({ orderId, sheet, side, version }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState('');
  const previous = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    setError('');
    api
      .previewBlob(orderId, sheet, side, RENDER_WIDTH, controller.signal)
      .then((next) => {
        if (cancelled) {
          URL.revokeObjectURL(next);
          return;
        }
        // Старую картинку отпускаем только когда пришла новая: иначе на время
        // загрузки лист мигал бы пустотой при каждом переключении параметра.
        if (previous.current) URL.revokeObjectURL(previous.current);
        previous.current = next;
        setUrl(next);
      })
      .catch((exc) => {
        if (!cancelled && exc.name !== 'AbortError') setError(exc.message);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [orderId, sheet, side, version]);

  useEffect(
    () => () => {
      if (previous.current) URL.revokeObjectURL(previous.current);
    },
    [],
  );

  if (error) return <div className="sheet sheet--error">{error}</div>;
  if (!url) return <div className="sheet sheet--loading" />;
  return (
    <div className="sheet">
      <img src={url} alt={`Лист ${sheet}, ${side === 'back' ? 'оборот' : 'лицо'}`} />
    </div>
  );
}
