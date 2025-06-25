// vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/ig-dashboard/',
  plugins: [react()],
  server: {
    proxy: {
      '/proxy/smart': {
        target: 'http://smart.who.int',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/proxy\/smart/, '')
      },
      '/proxy/fhir': {
        target: 'http://fhir.org',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/proxy\/fhir/, '')
      },
      '/proxy/githubio': {
        target: 'http://worldhealthorganization.github.io',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/proxy\/githubio/, '')
      }
    }
  }
})
