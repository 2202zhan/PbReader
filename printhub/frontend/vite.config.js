import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Сборка кладётся прямо в пакет: сервер отдаёт её сам, одним источником.
// Так у веб-аппа и API один домен — без CORS и без второго адреса, который
// пришлось бы отдельно пускать в телеграм.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../printhub/web',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // В разработке фронт живёт отдельно и ходит в питон через прокси —
    // origin остаётся один и тот же, поведение не расходится со сборкой.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
