import { isRecord } from './types'
import { validateEnvelope } from './validate'
import type { Result } from './types'

export class ApiError extends Error {
  constructor(public code:string, message:string, public status:number, public requestId='', public fields:unknown[]=[], public unknownResult=false) { super(message) }
}
export const apiMode = import.meta.env.VITE_API_MODE || 'real'
if (!['real','mock'].includes(apiMode) || (import.meta.env.PROD && apiMode==='mock')) throw new Error('模拟模式仅可用于显式开发预览')
let csrf = ''
export const setCsrf = (value:string) => { csrf=value }
const inflight = new Set<string>()
const uncertain = new Set<string>()
export function isUncertain(scope:string) { return uncertain.has(scope) }
export function resetTransport() { csrf=''; uncertain.clear() }
export interface Options { method?:string; body?:unknown; etag?:string|null; key?:string; signal?:AbortSignal; scope?:string }
export async function request<T>(path:string, options:Options={}):Promise<Result<T>> {
  const method=options.method || 'GET', writing=method!=='GET', scope=options.scope || `${method}:${path}`
  if (writing && (inflight.has(scope) || uncertain.has(scope))) throw new ApiError('RESULT_UNKNOWN','该操作正在提交或结果待核实，请先查看最新事实。',409,'',[],true)
  if (writing) inflight.add(scope)
  try {
    let response:Response
    if (import.meta.env.DEV && apiMode==='mock') {
      const { mockRequest }=await import('../mocks/transport')
      response=await mockRequest(path, options)
    } else {
      const headers=new Headers()
      if (options.body!==undefined && !(options.body instanceof FormData)) headers.set('Content-Type','application/json')
      if (writing && csrf) headers.set('X-CSRF-Token',csrf)
      if (options.etag) headers.set('If-Match',options.etag)
      if (options.key) headers.set('Idempotency-Key',options.key)
      try { response=await fetch(path,{method,headers,credentials:'include',signal:options.signal,body:options.body instanceof FormData ? options.body : options.body===undefined ? undefined : JSON.stringify(options.body)}) }
      catch(e) {
        if (e instanceof DOMException && e.name==='AbortError' && !writing) throw e
        if (writing) uncertain.add(scope)
        throw new ApiError('NETWORK_ERROR',writing?'连接中断，提交结果待核实。请查询原对象，勿重复提交。':'无法连接服务，请检查后端是否启动。',0,'',[],writing)
      }
    }
    let payload:unknown
    try { payload=await response.json() } catch { if(writing) uncertain.add(scope); throw new ApiError('PROTOCOL_ERROR','服务未返回约定的数据结构。',response.status,'',[],writing) }
    if (!isRecord(payload)) throw new ApiError('PROTOCOL_ERROR','响应格式不匹配。',response.status)
    try{validateEnvelope(payload)}catch{if(writing)uncertain.add(scope);throw new ApiError('PROTOCOL_ERROR','响应与约定契约不匹配，请保留现场并核实。',response.status,'',[],writing)}
    const id=typeof payload.request_id==='string'?payload.request_id:''
    // Health 503 is an intentional, readable data envelope.
    if ((!response.ok && !(path==='/health' && 'data' in payload)) || 'error' in payload) {
      const e=isRecord(payload.error)?payload.error:{}
      if(response.status===401) window.dispatchEvent(new Event('session-expired'))
      const unknown=writing && response.status>=500 && response.status!==503
      if(unknown) uncertain.add(scope)
      throw new ApiError(typeof e.code==='string'?e.code:'HTTP_ERROR',typeof e.message==='string'?e.message:'请求未完成。',response.status,id,Array.isArray(e.field_errors)?e.field_errors:[],unknown)
    }
    if (!('data' in payload) && !('items' in payload)) throw new ApiError('PROTOCOL_ERROR','响应缺少约定的 data 或 items。',response.status,id)
    return {data:('data' in payload?payload.data:payload) as T,etag:response.headers.get('ETag'),requestId:id}
  } finally { if(writing) inflight.delete(scope) }
}
export const get=<T>(path:string,signal?:AbortSignal)=>request<T>(path,{signal})
export const post=<T>(path:string,body?:unknown,options:Options={})=>request<T>(path,{...options,method:'POST',body})
export function queryString(values:Record<string,string|number|null|undefined>) { const q=new URLSearchParams(); for(const [k,v] of Object.entries(values)) if(v!==undefined&&v!==null&&v!=='') q.set(k,String(v)); return q.size?'?'+q.toString():'' }
