import { useRef, useState } from 'react';
import { haptic } from './telegram';

export default function Upload({ onPick, uploading, progress }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);

  const choose = () => {
    haptic();
    input.current?.click();
  };

  return (
    <>
      <div
        className={`drop${over ? ' over' : ''}`}
        onClick={choose}
        onKeyDown={(event) => (event.key === 'Enter' || event.key === ' ') && choose()}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          const file = event.dataTransfer.files?.[0];
          if (file) onPick(file);
        }}
        role="button"
        tabIndex={0}
      >
        <strong>{uploading ? 'Загружаю…' : 'Выбрать файл'}</strong>
        <span>PDF, Word, Excel, PowerPoint, RTF</span>
        {uploading && (
          <div className="progress">
            <i style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        )}
      </div>
      <input
        ref={input}
        type="file"
        hidden
        accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.rtf"
        onChange={(event) => {
          const file = event.target.files?.[0];
          // Сбрасываем значение: иначе повторный выбор того же файла не
          // вызовет change, и человеку будет казаться, что кнопка не работает.
          event.target.value = '';
          if (file) onPick(file);
        }}
      />
    </>
  );
}
