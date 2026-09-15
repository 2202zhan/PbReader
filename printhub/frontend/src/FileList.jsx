import { humanSize, pagesWord, shortDate } from './format';
import { DocumentIcon } from './icons';

function badge(file) {
  return (file.format || '?').slice(0, 4);
}

export default function FileList({ files, onDelete, busyId }) {
  if (!files.length) {
    return (
      <div className="empty">
        <span className="glyph"><DocumentIcon /></span>
        Пока ничего не загружено
      </div>
    );
  }

  return (
    <div>
      {files.map((file) => (
        <div className="file" key={file.id}>
          <div className="icon">{badge(file)}</div>
          <div className="body">
            <div className="name">{file.name}</div>
            <div className="meta">
              {[
                file.pages ? pagesWord(file.pages) : file.format_title,
                humanSize(file.size),
                shortDate(file.created_at),
              ]
                .filter(Boolean)
                .join(' · ')}
            </div>
          </div>
          <button
            type="button"
            disabled={busyId === file.id}
            onClick={() => onDelete(file)}
            aria-label={`Удалить ${file.name}`}
            title="Удалить"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
