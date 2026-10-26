import { isRecord } from './types'
function require(condition:unknown,message:string):asserts condition{if(!condition)throw new Error(message)}
export function validateEnvelope(payload:unknown):void{
 require(isRecord(payload),'响应必须是对象')
 require(typeof payload.request_id==='string'&&payload.request_id.length>0,'响应缺少 request_id')
 if('error'in payload){require(isRecord(payload.error)&&typeof payload.error.code==='string'&&typeof payload.error.message==='string'&&Array.isArray(payload.error.field_errors),'错误结构不匹配');return}
 if('items'in payload){require(Array.isArray(payload.items)&&typeof payload.has_more==='boolean'&&(payload.next_cursor===null||typeof payload.next_cursor==='string'),'游标分页结构不匹配');require(payload.has_more===(payload.next_cursor!==null),'游标与 has_more 不一致')}
 else require('data'in payload,'响应缺少 data')
 const visit=(v:unknown,depth=0):void=>{
  if(depth>40)throw new Error('响应嵌套过深')
  if(Array.isArray(v)){v.forEach(x=>visit(x,depth+1));return}
  if(!isRecord(v))return
  if('available_actions'in v){require(Array.isArray(v.available_actions),'权限投影必须是数组');for(const a of v.available_actions){require(isRecord(a)&&typeof a.action==='string'&&typeof a.allowed==='boolean','权限投影结构不匹配');if(!a.allowed)require(typeof a.reason_code==='string'&&a.reason_code.length>0,'拒绝动作缺少安全原因')}}
  if('version'in v&&'etl_plan'in v){require(isRecord(v.version)&&typeof v.version.id==='string'&&typeof v.version.is_immutable==='boolean','设计版本结构不匹配');require(v.etl_plan===null||isRecord(v.etl_plan),'ETL 方案结构不匹配');require(v.validation_report===null||(isRecord(v.validation_report)&&typeof v.validation_report.passed==='boolean'),'门禁结构不匹配')}
  if('run'in v&&'publication'in v)require(isRecord(v.run)&&typeof v.run.execution_status==='string'&&Number.isInteger(v.run.revision),'运行快照结构不匹配')
  Object.values(v).forEach(x=>visit(x,depth+1))
 }
 visit('data'in payload?payload.data:payload.items)
}
