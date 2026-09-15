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
