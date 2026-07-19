import axios from 'axios'
import { attachEnvelopeInterceptor, wrapTyped } from './client'

// The files blueprint is mounted at the bare /files prefix, not /api/v1
// (see drawbridge/main.py's blueprint registration and vite.config.js's
// dev proxy) — hence a second instance rather than reusing client.ts.
const filesClient = axios.create({
  baseURL: '/files',
  withCredentials: true,
})

attachEnvelopeInterceptor(filesClient)

export default wrapTyped(filesClient)
