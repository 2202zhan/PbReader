// Разговор с сервером.
//
// Токен сессии живёт в памяти вкладки и в localStorage: веб-апп телеграма
// перезагружается при каждом открытии, и без этого пришлось бы входить заново
// каждый раз. В localStorage лежит только он — ни файлов, ни имён.

const TOKEN_KEY = 'printhub.token';

let token = null;
try {
  token = localStorage.getItem(TOKEN_KEY);
} catch {
  /* приватное окно — обойдёмся памятью */
}

export function setToken(value) {
  token = value;
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* не страшно: сессия просто не переживёт перезагрузку */
  }
}

export function hasToken() {
  return Boolean(token);
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.json);
    delete options.json;
  }

  const response = await fetch(`/api${path}`, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    if (response.status === 401) setToken(null);
    throw new ApiError(data?.error || `Ошибка ${response.status}`, response.status);
  }
  return data;
}

export const api = {
  loginTelegram: (initData) =>
    request('/auth/telegram', { method: 'POST', json: { init_data: initData } }),
  loginDev: () => request('/auth/dev', { method: 'POST', json: { user_id: 1 } }),
  me: () => request('/me'),
  health: () => request('/health'),
  files: () => request('/files'),
  printers: () => request('/printers'),

  orders: () => request('/orders'),
  order: (id) => request(`/orders/${id}`),
  createOrder: (body) => request('/orders', { method: 'POST', json: body }),
  updateOrder: (id, body) => request(`/orders/${id}`, { method: 'PATCH', json: body }),
  confirmOrder: (id) => request(`/orders/${id}/confirm`, { method: 'POST' }),
  pay: (id) => request(`/orders/${id}/pay`, { method: 'POST' }),
  paymentState: (id) => request(`/orders/${id}/payment`),
  // Оплата понарошку: сервер принимает это только в режиме разработки.
  devPay: (externalId, result) =>
    request(`/payments/dev/${externalId}/${result}`, { method: 'POST' }),
  release: (id) => request(`/orders/${id}/release`, { method: 'POST' }),
  cancelOrder: (id) => request(`/orders/${id}`, { method: 'DELETE' }),

  adminPrinters: () => request('/admin/printers'),
  adminCreatePrinter: (body) => request('/admin/printers', { method: 'POST', json: body }),
  adminUpdatePrinter: (id, body) =>
    request(`/admin/printers/${id}`, { method: 'PATCH', json: body }),
  adminTariffs: () => request('/admin/tariffs'),
  adminSetTariff: (scope, body) =>
    request(`/admin/tariffs/${scope}`, { method: 'PUT', json: body }),
  adminOrders: () => request('/admin/orders'),

  async pageThumb(orderId, page, signal) {
    const response = await fetch(`/api/orders/${orderId}/pages/${page}?width=240`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    });
    if (!response.ok) throw new ApiError(`Страница не отрисовалась`, response.status);
    return URL.createObjectURL(await response.blob());
  },

  // Предпросмотр тянется запросом, а не подставляется в <img src>.
  //
  // У <img> нет заголовков, поэтому пришлось бы класть токен сессии в адрес
  // картинки — а адреса попадают в журналы сервера, в историю браузера и в
  // Referer. Токен оттуда достать проще, чем кажется, а предпросмотр — это
  // содержимое чужого документа.
  async previewBlob(orderId, sheet, side, width, signal) {
    const query = new URLSearchParams({ side, width: String(width) });
    const response = await fetch(`/api/orders/${orderId}/preview/${sheet}?${query}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    });
    if (!response.ok) throw new ApiError(`Лист не отрисовался (${response.status})`, response.status);
    return URL.createObjectURL(await response.blob());
  },
  deleteFile: (id) => request(`/files/${id}`, { method: 'DELETE' }),
  upload(file, onProgress) {
    // fetch не умеет сообщать о ходе отправки, а файл на 40 МБ по мобильной
    // сети идёт достаточно долго, чтобы человек решил, что всё зависло.
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('upload', file, file.name);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/files');
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
      };
      xhr.onload = () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch {
          /* пустой или битый ответ разберём ниже */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiError(data?.error || `Ошибка ${xhr.status}`, xhr.status));
      };
      xhr.onerror = () => reject(new ApiError('Не удалось связаться с сервером', 0));
      xhr.send(form);
    });
  },
};
