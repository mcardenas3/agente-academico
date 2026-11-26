import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// En desarrollo (npm run dev), las llamadas a /api van a la API de FastAPI en el puerto 8080.
// En producción la misma API sirve esta interfaz ya compilada, así que no hace falta.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8080', '/articulo.pdf': 'http://127.0.0.1:8080' },
  },
})
