import { useCallback, useEffect, useState } from 'react';
import { api, hasToken, setToken } from './api';
import { initData, isTelegram, ready } from './telegram';
import FileList from './FileList';
import Upload from './Upload';
import { filesWord, pagesWord } from './format';
import { FolderIcon, PersonIcon, PrinterIcon } from './icons';

const TABS = [
  { key: 'home', Icon: PrinterIcon, title: 'Главная' },
  { key: 'files', Icon: FolderIcon, title: 'Мои файлы' },
  { key: 'profile', Icon: PersonIcon, title: 'Профиль' },
];

export default function App() {
  const [tab, setTab] = useState('home');
  const [profile, setProfile] = useState(null);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('starting');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [busyId, setBusyId] = useState(null);
  const [health, setHealth] = useState(null);

  const refresh = useCallback(async () => {
    const [me, list] = await Promise.all([api.me(), api.files()]);
    setProfile(me);
    setFiles(list.files);
  }, []);

  useEffect(() => {
    ready();

    (async () => {
      try {
        const state = await api.health();
        setHealth(state);

        if (!hasToken()) {
          if (isTelegram) {
            setToken((await api.loginTelegram(initData())).token);
          } else if (state.dev_login) {
            // Вход без телеграма — только когда сервер сам его разрешил.
            setToken((await api.loginDev()).token);
          } else {
            setStatus('needs-telegram');
            return;
          }
        }

        await refresh();
        setStatus('ready');
      } catch (exc) {
        // Токен мог протухнуть за ночь — тогда пробуем войти заново, один раз.
        if (exc.status === 401 && isTelegram) {
          try {
            setToken((await api.loginTelegram(initData())).token);
            await refresh();
            setStatus('ready');
            return;
          } catch (retry) {
            setError(retry.message);
          }
        } else {
          setError(exc.message);
        }
        setStatus('failed');
      }
    })();
  }, [refresh]);

  async function pick(file) {
    setError('');
    setUploading(true);
    setProgress(0);
    try {
      const uploaded = await api.upload(file, setProgress);
      setFiles((current) => [uploaded, ...current]);
      setProfile((current) =>
        current ? { ...current, files_count: (current.files_count || 0) + 1 } : current,
      );
      setTab('files');
    } catch (exc) {
      setError(exc.message);
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }

  async function remove(file) {
    setError('');
    setBusyId(file.id);
    try {
      await api.deleteFile(file.id);
      setFiles((current) => current.filter((item) => item.id !== file.id));
      setProfile((current) =>
        current ? { ...current, files_count: Math.max(0, (current.files_count || 1) - 1) } : current,
      );
    } catch (exc) {
      setError(exc.message);
    } finally {
      setBusyId(null);
    }
  }

  if (status === 'starting') {
    return <div className="app"><div className="screen"><p className="hint">Загрузка…</p></div></div>;
  }

  if (status === 'needs-telegram') {
    return (
      <div className="app">
        <div className="screen">
          <h1>PrintHub</h1>
          <p className="hint">Это приложение открывается из телеграма.</p>
          <div className="notice warn">
            Вход возможен только через телеграм — подпись запуска подтверждает, кто вы.
            Чтобы работать в обычном браузере при разработке, запустите сервер
            с <code>PRINTHUB_DEV_LOGIN=1</code>.
          </div>
        </div>
      </div>
    );
  }

  const totalPages = files.reduce((sum, file) => sum + (file.pages || 0), 0);

  return (
    <div className="app">
      <div className="screen">
        {error && <div className="notice error">{error}</div>}
        {health?.dev_login && (
          <div className="notice warn">
            Режим разработки: вход без телеграма включён. На боевом сервере он невозможен.
          </div>
        )}

        {tab === 'home' && (
          <>
            <h1>Печать документов</h1>
            <p className="hint">
              Загрузите файл, выберите параметры и заберите распечатку у принтера.
            </p>
            <Upload onPick={pick} uploading={uploading} progress={progress} />
            {files.length > 0 && (
              <>
                <h2>Последние файлы</h2>
                <FileList files={files.slice(0, 3)} onDelete={remove} busyId={busyId} />
              </>
            )}
          </>
        )}

        {tab === 'files' && (
          <>
            <h1>Мои файлы</h1>
            <p className="hint">
              {files.length
                ? `${filesWord(files.length)}${totalPages ? `, всего ${pagesWord(totalPages)}` : ''}`
                : 'Загруженные документы появятся здесь'}
            </p>
            <Upload onPick={pick} uploading={uploading} progress={progress} />
            <h2>Все файлы</h2>
            <FileList files={files} onDelete={remove} busyId={busyId} />
          </>
        )}

        {tab === 'profile' && (
          <>
            <h1>Профиль</h1>
            <p className="hint">Данные берутся из телеграма — менять их здесь незачем.</p>
            <div className="rows">
              <div className="row">
                <span>Имя</span>
                <span>{[profile?.first_name, profile?.last_name].filter(Boolean).join(' ') || '—'}</span>
              </div>
              <div className="row">
                <span>Ник</span>
                <span>{profile?.username ? `@${profile.username}` : '—'}</span>
              </div>
              <div className="row">
                <span>Номер</span>
                <span>{profile?.id}</span>
              </div>
              <div className="row">
                <span>Файлов</span>
                <span>{profile?.files_count ?? 0}</span>
              </div>
            </div>

            <h2>Заказы</h2>
            <div className="card hint" style={{ marginTop: 0 }}>
              История заказов появится, когда заработают оплата и печать.
            </div>
          </>
        )}
      </div>

      <nav className="tabs">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={tab === item.key ? 'active' : ''}
            onClick={() => setTab(item.key)}
          >
            <span className="glyph"><item.Icon /></span>
            {item.title}
          </button>
        ))}
      </nav>
    </div>
  );
}
