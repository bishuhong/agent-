import { createRouter, createWebHistory } from 'vue-router'
import { clearSession, restoreSession, selectProject, session } from './composables/session'
export const router=createRouter({history:createWebHistory(),routes:[
  {path:'/login',component:()=>import('./pages/LoginPage.vue'),meta:{title:'登录'}},
  {path:'/projects',component:()=>import('./pages/ProjectsPage.vue'),meta:{title:'项目与成员'}},
  {path:'/projects/:projectId/overview',component:()=>import('./pages/OverviewPage.vue'),meta:{title:'总览工作台'}},
  {path:'/projects/:projectId/assets',component:()=>import('./pages/AssetsPage.vue'),meta:{title:'数据连接与资产'}},
  {path:'/projects/:projectId/pipelines',component:()=>import('./pages/PipelinesPage.vue'),meta:{title:'流水线'}},
  {path:'/projects/:projectId/pipelines/:pipelineId/studio/:versionId',component:()=>import('./pages/StudioPage.vue'),meta:{title:'Pipeline Studio'}},
  {path:'/projects/:projectId/versions/:versionId',component:()=>import('./pages/VersionPage.vue'),meta:{title:'版本与准备'}},
  {path:'/projects/:projectId/preparations',component:()=>import('./pages/PreparationsPage.vue'),meta:{title:'准备与审批'}},
  {path:'/projects/:projectId/preparations/:id',component:()=>import('./pages/PreparationPage.vue'),meta:{title:'准备单详情'}},
  {path:'/projects/:projectId/runs',component:()=>import('./pages/RunListPage.vue'),meta:{title:'运行中心'}},
  {path:'/projects/:projectId/runs/:id',component:()=>import('./pages/RunDetailPage.vue'),meta:{title:'运行详情'}},
  {path:'/projects/:projectId/governance/:section(audit|benchmarks|evolution)',component:()=>import('./pages/GovernancePage.vue'),meta:{title:'安全治理'}},
  {path:'/health-status',component:()=>import('./pages/HealthPage.vue'),meta:{title:'组件健康'}},
  {path:'/:pathMatch(.*)*',redirect:()=>session.project?`/projects/${session.project.id}/overview`:'/projects'}
]})
router.beforeEach(async to=>{
  if(to.path==='/login'||to.path==='/health-status')return true
  try {
    if(!session.identity) await restoreSession()
    const id=to.params.projectId
    if(typeof id==='string') await selectProject(id)
    if(to.path==='/projects'&&session.projects.length===1&&!to.query.all) return `/projects/${session.projects[0]!.id}/overview`
  }catch(e){if(e instanceof Error && 'status'in e && e.status===401)clearSession();return {path:'/login',query:{return:to.fullPath}}}
})
router.afterEach(to=>{document.title=`${String(to.meta.title||'工作台')} · ETL Agent`;requestAnimationFrame(()=>document.querySelector<HTMLElement>('main h1')?.focus())})
