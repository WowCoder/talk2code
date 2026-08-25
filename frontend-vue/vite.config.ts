import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:5001'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    server: {
      port: 5100,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      target: 'es2020',
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            // Split CodeMirror into a separate chunk for better caching
            if (id.includes('codemirror') || id.includes('@codemirror')) {
              return 'codemirror'
            }
          },
        },
      },
    },
  }
})
