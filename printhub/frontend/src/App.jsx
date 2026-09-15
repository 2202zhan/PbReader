import { useCallback, useEffect, useState } from 'react';
import { api, hasToken, setToken } from './api';
import { initData, isTelegram, ready } from './telegram';
import FileList from './FileList';
import Upload from './Upload';
import Orders from './Orders';
import OrderWizard from './OrderWizard';
import PayScreen from './PayScreen';
import Admin from './Admin';
import { filesWord, pagesWord } from './format';
import { CogIcon, FolderIcon, PersonIcon, PrinterIcon, ReceiptIcon } from './icons';

export default function App() {
  const [tab, setTab] = useState('home');
  const [profile, setProfile] = useState(null);
  const [files, setFiles] = useState([]);
  const [orders, setOrders] = useState([]);
  const [printers, setPrinters] = useState([]);
  const [openOrder, setOpenOrder] = useState(null);
  const [payOrder, setPayOrder] = useState(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('starting');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [busyId, setBusyId] = useState(null);
  const [health, setHealth] = useState(null);

  const refresh = useCallback(async () => {
    const [me, fileList, orderList, printerList] = await Promise.all([
      api.me(),
      api.files(),
      api.orders(),
      api.printers(),
    ]);
    setProfile(me);
    setFiles(fileList.files);
    setOrders(orderList.orders);
    setPrinters(printerList.printers);
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
            setToken((await api.loginDev()).token);
          } else {
            setStatus('needs-telegram');
            return;
          }
        }

        await refresh();
        setStatus('ready');
      } catch (exc) {
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
      await print(uploaded);
    } catch (exc) {
      setError(exc.message);
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }

  async function print(file) {
    setError('');
    try {
      const order = await api.createOrder({
        file_id: file.id,
        printer_id: printers[0]?.id || null,
      });
      setOpenOrder(order);
    } catch (exc) {
      setError(exc.message);
      setTab('files');
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

  async function afterConfirm(order) {
    setOpenOrder(null);
    setPayOrder(order);
    setOrders(await api.orders().then((data) => data.orders));
  }

  async function afterPaid() {
    setOrders(await api.orders().then((data) => data.orders));
  }

  async function closePay() {
    setPayOrder(null);
    setTab('orders');
    setOrders(await api.orders().then((data) => data.orders));
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

  const tabs = [
    { key: 'home', Icon: PrinterIcon, title: 'Главная' },
    { key: 'files', Icon: FolderIcon, title: 'Файлы' },
    { key: 'orders', Icon: ReceiptIcon, title: 'Заказы' },
    { key: 'profile', Icon: PersonIcon, title: 'Профиль' },
  ];
  if (profile?.is_admin) tabs.push({ key: 'admin', Icon: CogIcon, title: 'Админка' });

  const totalPages = files.reduce((sum, file) => sum + (file.pages || 0), 0);
  const waiting = orders.filter((order) => order.state === 'awaiting_payment').length;

  return (
    <div className="app">
      <div className="screen">
        {payOrder ? (
          <PayScreen order={payOrder} onPaid={afterPaid} onClose={closePay} />
        ) : openOrder ? (
          <OrderWizard
            order={openOrder}
            onClose={() => setOpenOrder(null)}
            onConfirmed={afterConfirm}
          />
        ) : (
          <>
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
                    <FileList files={files.slice(0, 3)} onDelete={remove} onPrint={print}
                              busyId={busyId} />
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
                <FileList files={files} onDelete={remove} onPrint={print} busyId={busyId} />
              </>
            )}

            {tab === 'orders' && (
              <>
                <h1>Заказы</h1>
                <p className="hint">
                  {waiting ? `${waiting} ждёт оплаты` : 'Здесь видно, что с вашими заказами'}
                </p>
                <Orders
                  orders={orders}
                  onOpen={(order) => {
                    if (order.editable) setOpenOrder(order);
                    else if (order.state === 'awaiting_payment') setPayOrder(order);
                  }}
                />
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
                  <div className="row">
                    <span>Заказов</span>
                    <span>{orders.length}</span>
                  </div>
                </div>
              </>
            )}

            {tab === 'admin' && <Admin />}
          </>
        )}
      </div>

      {!openOrder && !payOrder && (
        <nav className="tabs">
          {tabs.map((item) => (
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
      )}
    </div>
  );
}
