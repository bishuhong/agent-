import { afterEach,describe,it,expect,vi } from 'vitest'
import { request,setCsrf,resetTransport,ApiError } from '../src/api/client'
import { validateEnvelope } from '../src/api/validate'
afterEach(()=>{vi.unstubAllGlobals();resetTransport()})
const result=(data:unknown,status=200)=>new Response(JSON.stringify({data,request_id:'test-id'}),{status,headers:{ETag:'"server-etag"'}})
describe('真实 API 安全边界',()=>{
 it('携带 HttpOnly 会话，不尝试读取 cookie，并提交稳定 CSRF 和服务器 ETag',async()=>{const fetcher=vi.fn().mockResolvedValue(result({id:'known'}));vi.stubGlobal('fetch',fetcher);setCsrf('session-csrf');await request('/api/v1/sample',{method:'PUT',body:{value:'x'},etag:'"existing-etag"'});const options=fetcher.mock.calls[0]![1] as RequestInit;expect(options.credentials).toBe('include');expect((options.headers as Headers).get('X-CSRF-Token')).toBe('session-csrf');expect((options.headers as Headers).get('If-Match')).toBe('"existing-etag"')})
 it('保留503健康结果，不回退模拟数据',async()=>{const fetcher=vi.fn().mockResolvedValue(result({status:'degraded',components:[]},503));vi.stubGlobal('fetch',fetcher);expect((await request<{status:string}>('/health')).data.status).toBe('degraded');expect(fetcher).toHaveBeenCalledTimes(1)})
 it('写入断网会锁定该意图，不自动重试或生成第二次请求',async()=>{const fetcher=vi.fn().mockRejectedValue(new TypeError('offline'));vi.stubGlobal('fetch',fetcher);await expect(request('/api/v1/preparations/known/commit',{method:'POST',body:{},key:'original-key'})).rejects.toMatchObject({unknownResult:true});await expect(request('/api/v1/preparations/known/commit',{method:'POST',body:{},key:'new-key'})).rejects.toMatchObject({code:'RESULT_UNKNOWN'});expect(fetcher).toHaveBeenCalledTimes(1)})
 it('读请求失败不会生成成功结果或空列表',async()=>{vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('offline')));await expect(request('/api/v1/projects')).rejects.toBeInstanceOf(ApiError)})
 it('不自动重试412或伪造乐观业务状态',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({error:{code:'REVISION_MISMATCH',message:'snapshot changed',field_errors:[],retryable:false},request_id:'etag-conflict'}),{status:412}));vi.stubGlobal('fetch',fetcher);await expect(request('/api/v1/known',{method:'POST'})).rejects.toMatchObject({code:'REVISION_MISMATCH',requestId:'etag-conflict'});expect(fetcher).toHaveBeenCalledTimes(1)})
 it('拒绝缺少关联信息的响应',()=>{expect(()=>validateEnvelope({data:[]})).toThrow('request_id')})
 it('拒绝不一致的游标分页',()=>{expect(()=>validateEnvelope({request_id:'r',items:[],has_more:true,next_cursor:null})).toThrow()})
 it('权限投影不能用字符串 true 冒充布尔值',()=>{expect(()=>validateEnvelope({request_id:'r',data:{available_actions:[{action:'commit',allowed:'true'}]}})).toThrow()})
 it('拒绝缺少安全原因的禁止动作',()=>{expect(()=>validateEnvelope({request_id:'r',data:{available_actions:[{action:'commit',allowed:false}]}})).toThrow()})
})
