const labels:Record<string,string>={enabled:'可用',active:'有效',draft:'草稿',ready:'已就绪',queued:'排队中',running:'运行中',waiting_input:'待澄清',validating:'校验中',repairing:'修复中',succeeded:'成功',failed:'失败',cancelled:'已取消',cancel_requested:'取消待确认',reconciling:'结果待核实',authorization_expired:'授权已过期',pending:'待审批',pending_approval:'待审批',approved:'已批准',rejected:'已拒绝',valid:'有效',stale:'事实已变化',blocked:'已阻断',expired:'已过期',passed:'已通过',unknown:'未知',not_published:'未发布',publishing:'发布中',published:'已发布',not_ready:'未就绪',degraded:'部分依赖未就绪',revoked:'已撤销',requested:'已受理',within_budget:'预算内',exceeded:'预算超限',maker:'设计人员',operator:'操作员',data_checker:'数据审批人',security_checker:'安全审批人',auditor:'审计人员',execute_etl:'正式执行',isolated_trial:'隔离试运行',rollback:'回滚',run_l2_benchmark:'L2 真实评测'}
export const label=(value:unknown)=>typeof value==='string'?(labels[value] || value):'未知'
export const count=(value:unknown)=>typeof value==='string'&&/^\d+$/.test(value)?value.replace(/\B(?=(\d{3})+(?!\d))/g,','):'—'
export const short=(v:unknown,n=8)=>typeof v==='string'?v.slice(0,n):'—'
export const date=(v:unknown)=>typeof v==='string'&&!Number.isNaN(Date.parse(v))?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}).format(new Date(v)):'尚未产生'
export function percentage(v:unknown){if(typeof v!=='string'||!/^\d+(\.\d+)?$/.test(v))return '—'; const [a,b='']=v.split('.');const raw=BigInt(a!)*100n+BigInt((b+'00').slice(0,2));return `${raw}.${(b+'0000').slice(2,4).replace(/0+$/,'')||'0'}%`}
export function integerText(v:string){if(!/^(0|[1-9]\d*)$/.test(v))throw new Error('请输入不含小数和指数的非负整数');return v}
export function ratioText(v:string){if(!/^(0|1)(\.\d+)?$/.test(v)||(v.startsWith('1.')&&/[1-9]/.test(v.slice(2))))throw new Error('比例应为0到1之间的精确十进制文本');return v}
export function localToInstant(local:string,zone:string):string {
 if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(local))throw new Error('请选择完整的本地日期和时间')
 const formatter=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'})
 const parts=(ms:number)=>{const p=Object.fromEntries(formatter.formatToParts(new Date(ms)).map(v=>[v.type,v.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`}
 const naive=Date.parse(local+':00Z');if(Number.isNaN(naive)||new Date(naive).toISOString().slice(0,16)!==local)throw new Error('日期无效')
 const candidates=new Set<number>()
 for(let h=-36;h<=36;h+=6){const probe=naive+h*3600000;const offset=Date.parse(parts(probe)+'Z')-probe;const candidate=naive-offset;if(parts(candidate)===local+':00')candidates.add(candidate)}
 if(candidates.size!==1)throw new Error(candidates.size?'该时区的本地时间重复，请选择不歧义的时刻':'该本地时间在所选时区不存在')
 return new Date([...candidates][0]!).toISOString()
}
export function barPercent(part:string,total:string){try{const p=BigInt(part),t=BigInt(total);return t>0n?Number(p*100n/t>100n?100n:p*100n/t):0}catch{return 0}}
export function instantToLocal(instant:string,zone:string){const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(instant)).map(p=>[p.type,p.value]));return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`}
