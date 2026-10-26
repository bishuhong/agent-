import fixtures from './fixtures.json'
import type { Options } from '../api/client'
import type { Design,Pipeline,Version,Model,Project } from '../api/types'
import { isRecord } from '../api/types'
const db=structuredClone(fixtures)
const designs=structuredClone(fixtures.designs) as unknown as Record<string,Design>
const pipelines=structuredClone(fixtures.pipelines) as Pipeline[]
const versions=structuredClone(fixtures.versions) as Version[]
let agent=structuredClone(fixtures.agent) as Model<'AgentRunResponse'>
const project=fixtures.project as Project
function response(data:unknown,status=200,list=false){return new Response(JSON.stringify(list?{items:data,next_cursor:null,has_more:false,request_id:'explicit-preview'}:{data,request_id:'explicit-preview'}),{status,headers:{'Content-Type':'application/json','ETag':'"explicit-preview-etag"'}})}
function failure(message='交互预览未实现该模拟动作，不会连接真实服务。',status=503){return new Response(JSON.stringify({error:{code:'DEPENDENCY_UNAVAILABLE',message,field_errors:[],retryable:false},request_id:'explicit-preview'}),{status,headers:{'Content-Type':'application/json'}})}
export async function mockRequest(path:string,options:Options):Promise<Response>{
 if(import.meta.env.PROD || import.meta.env.VITE_API_MODE!=='mock')throw new Error('禁止在真实模式加载模拟传输')
 const url=new URL(path,'http://explicit-preview.invalid'),p=url.pathname,method=options.method||'GET',body=isRecord(options.body)?options.body:{}
 if(options.signal?.aborted)throw new DOMException('Aborted','AbortError')
 if(method==='POST'){
  if(p==='/api/v1/auth/login')return response({user:db.identity.user,csrf_token:db.identity.csrf_token,expires_at:db.identity.expires_at})
  if(p==='/api/v1/auth/logout')return response({logged_out:true})
  if(p==='/api/v1/pipelines'){
   if(typeof body.name!=='string'||typeof body.code!=='string')return failure('模拟请求不符合契约',422)
   const pipeline:Pipeline={...pipelines[0]!,id:crypto.randomUUID(),name:body.name,code:body.code,status:'draft'}
   const version:Version={...versions[0]!,id:crypto.randomUUID(),pipeline_id:pipeline.id,is_immutable:false,artifact_digest:null}
   pipelines.unshift(pipeline);versions.unshift(version)
   designs[version.id]={version,etl_plan:null,hocon_text:null,validation_report:null,available_actions:project.available_actions}
   return response({pipeline,candidate_version_id:version.id},201)
  }
  if(p.endsWith('/generation')){
   const id=p.split('/')[4]!,previous=designs[id]
   if(!previous)return failure('模拟版本不存在',404)
   const ready=structuredClone(Object.values(designs).find(v=>v.etl_plan!==null)!)
   designs[id]={...ready,version:{...previous.version,revision:previous.version.revision+1},validation_report:ready.validation_report?{...ready.validation_report,version_id:id}:null,available_actions:previous.available_actions}
   agent={...agent,id:crypto.randomUUID(),version_id:id,prompt:String(body.prompt||'模拟需求')}
   return response(agent,202)
  }
  if(p.endsWith('/trials'))return response(Object.values(designs)[0]!.validation_report)
  return failure()
 }
 if(method!=='GET')return failure()
 if(p==='/health')return response(db.health,503)
 if(p==='/api/v1/auth/me')return response(db.identity)
 if(p==='/api/v1/projects')return response([db.project],200,true)
 if(p===`/api/v1/projects/${db.project.id}`)return response(db.project)
 if(p.endsWith('/overview'))return response(db.overview)
 if(p.endsWith('/ledger/verification'))return response({project_id:db.project.id,checked_through_sequence:'3',result:'not_verified',checkpoint_id:null,verified_at:null})
 if(p.includes('/projects/')){
  const resource=p.split('/').at(-1)
  const lists:Record<string,unknown[]>={pipelines,connections:db.connections,'file-assets':[],'metadata-profiles':db.profiles,preparations:db.preparationSummaries,'execution-runs':db.runSummaries,'audit-events':db.audit,memberships:db.memberships,'role-grants':[],'benchmark-datasets':db.datasets,'benchmark-runs':[],'evolution-candidates':[],'evolution-authorizations':[]}
  if(resource&&resource in lists){let rows=lists[resource]!;const search=url.searchParams.get('query');if(search)rows=rows.filter(r=>isRecord(r)&&typeof r.name==='string'&&r.name.includes(search));return response(rows,200,true)}
 }
 if(p.match(/\/pipelines\/[^/]+\/versions$/))return response(versions.filter(v=>v.pipeline_id===p.split('/')[4]),200,true)
 if(p.match(/\/versions\/[^/]+\/design$/)){return designs[p.split('/')[4]!] ? response(designs[p.split('/')[4]!]):failure('模拟版本不存在',404)}
 if(p.match(/\/connections\/[^/]+$/)){const c=db.connections.find(c=>c.id===p.split('/')[4]);return c?response(c):failure('模拟连接不存在',404)}
 if(p.match(/\/agent-runs\/[^/]+$/))return response(agent)
 if(p.endsWith('/clarifications'))return response([],200,true)
 if(p.match(/\/preparations\/[^/]+$/))return response(db.preparation)
 if(p.match(/\/execution-runs\/[^/]+$/)){const runs=db.runs as Record<string,unknown>;return runs[p.split('/')[4]!] ?response(runs[p.split('/')[4]!]):failure('模拟运行不存在',404)}
 if(p.endsWith('/diagnostic-report')||p.endsWith('/quality-report'))return response(null)
 if(p.endsWith('/logs')||p.endsWith('/operations'))return response([],200,true)
 return failure('该模拟资源尚未定义。真实模式不会回退到预览。',404)
}
