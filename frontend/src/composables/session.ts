import { computed, reactive } from 'vue'
import { get, post, queryString, resetTransport, setCsrf } from '../api/client'
import type { Identity, Page, Project, Action } from '../api/types'
export const session=reactive({identity:null as Identity|null, projects:[] as Project[], project:null as Project|null, epoch:0, restored:false})
export const displayName=computed(()=>session.identity?.user.display_name || '访客')
export function allowed(action:string, actions?:Action[]) { return (actions || session.project?.available_actions || []).some(a=>a.action===action && a.allowed===true) }
export async function restoreSession() {
  const {data}=await get<Identity>('/api/v1/auth/me')
  if(typeof data.csrf_token!=='string'||!Array.isArray(data.project_memberships)) throw new Error('身份契约不匹配')
  session.identity=data; setCsrf(data.csrf_token)
  const projects:Project[]=[],seen=new Set<string>();let cursor:string|null=null
  do {
    const page:Page<Project>=(await get<Page<Project>>('/api/v1/projects'+queryString({limit:100,cursor}))).data
    projects.push(...page.items);cursor=page.has_more?page.next_cursor:null
    if(cursor&&seen.has(cursor)) throw new Error('项目分页游标重复，请联系管理员核实')
    if(cursor)seen.add(cursor)
  }while(cursor)
  session.projects=projects
  session.restored=true
}
export async function selectProject(id:string) {
  if(session.project?.id===id) return
  const epoch=++session.epoch; session.project=null
  if(!session.projects.some(p=>p.id===id)) throw new Error('当前账号无法访问该项目')
  const project=(await get<Project>(`/api/v1/projects/${id}`)).data
  if(epoch===session.epoch)session.project=project
}
export function clearSession(){ session.identity=null;session.project=null;session.projects=[];session.restored=false;session.epoch++;resetTransport() }
export async function logout(){ try {await post('/api/v1/auth/logout')} finally {clearSession()} }
