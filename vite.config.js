import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
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
        target: 'http://localhost:8001',
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
});

