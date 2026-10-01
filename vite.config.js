import { defineConfig, loadEnv } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Dev only: point the demo page at a real backend (e.g. a PR preview) by
// setting VITE_API_TARGET and VITE_DEV_USER_TOKEN in .env.local (gitignored).
// Without them it talks to the mock server on :8001.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd());
  process.env.VITE_DEV_USER_TOKEN = env.VITE_DEV_USER_TOKEN || 'demo-user-1234';
  return {
    plugins: [
      svelte({
        compilerOptions: {
          customElement: true
        }
      })
    ],
    server: {
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET || 'http://localhost:8001',
          changeOrigin: true
        }
      }
    },
    build: {
      lib: {
        entry: 'src/main.js',
        name: 'LCChatbot',
        fileName: 'lc-chatbot',
        formats: ['es', 'umd']
      },
      rollupOptions: {
        output: {
          inlineDynamicImports: true
        }
      },
      minify: 'esbuild',
      target: 'es2020'
    },
    define: {
      'process.env.NODE_ENV': JSON.stringify('production')
    }
  };
});
