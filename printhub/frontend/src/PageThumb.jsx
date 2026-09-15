import { useEffect, useRef, useState } from 'react';
import { api } from './api';

/** Миниатюра страницы. Рисуется сервером тем же расчётом, что и печать. */
export default function PageThumb({ orderId, page, selected, disabled, onToggle }) {
  const [url, setUrl] = useState(null);
  const current = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    api
      .pageThumb(orderId, page, controller.signal)
      .then((next) => {
        if (!alive) {
          URL.revokeObjectURL(next);
          return;
        }
        if (current.current) URL.revokeObjectURL(current.current);
        current.current = next;
        setUrl(next);
      })
      .catch(() => {});
    return () => {
      alive = false;
      controller.abort();
    };
  }, [orderId, page]);

  useEffect(
    () => () => {
      if (current.current) URL.revokeObjectURL(current.current);
    },
    [],
  );

  return (
    <button
      type="button"
      className={`page-card${selected ? ' on' : ''}`}
      disabled={disabled}
      onClick={onToggle}
      aria-pressed={selected}
    >
      {url ? <img src={url} alt={`Страница ${page}`} /> : <div className="skeleton" />}
      <span className="tick">✓</span>
      <span className="number">{page}</span>
    </button>
  );
}
