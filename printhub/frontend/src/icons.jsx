// Иконки рисуются, а не берутся эмодзи.
//
// Эмодзи несут собственный цвет и собственную форму шрифта: 🖨 приходит
// монохромным контуром, 📁 — жёлтой, 👤 — синей. Из-за этого активная вкладка
// ничем не выделялась: подсветка цветом до эмодзи не доходит. У SVG цвет берётся
// из currentColor, поэтому состояние видно, а набор выглядит единым.

const common = {
  width: 22,
  height: 22,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
};

export function PrinterIcon() {
  return (
    <svg {...common}>
      <path d="M7 9V4h10v5" />
      <rect x="3" y="9" width="18" height="8" rx="2" />
      <path d="M7 14h10v6H7z" />
    </svg>
  );
}

export function FolderIcon() {
  return (
    <svg {...common}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

export function PersonIcon() {
  return (
    <svg {...common}>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5 20c0-3.6 3.1-5.6 7-5.6s7 2 7 5.6" />
    </svg>
  );
}

export function DocumentIcon() {
  return (
    <svg {...common} width={34} height={34}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}
