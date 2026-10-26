import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig(({mode,command}) => {
  const env = loadEnv(mode, process.cwd(), '')
  if(command==='build'&&env.VITE_API_MODE==='mock')throw new Error('禁止构建生产模拟版本；预览请使用开发服务器 --mode preview')
  return { plugins:[vue()], server:{port:5173,strictPort:true,proxy:{'/api':{target:env.BACKEND_PROXY || 'http://127.0.0.1:8000',changeOrigin:false},'^/health$':{target:env.BACKEND_PROXY || 'http://127.0.0.1:8000',changeOrigin:false}}},build:{sourcemap:false} }
})
