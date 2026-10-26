<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from './components/Icon.vue'
import { session, displayName, logout, clearSession } from './composables/session'
import { apiMode } from './api/client'
const route=useRoute(),router=useRouter(),menu=ref(false),logoutError=ref('')
const pid=computed(()=>session.project?.id||'')
const groups=[{title:'工作空间',items:[['overview','grid','总览工作台'],['assets','database','数据连接与资产'],['pipelines','flow','流水线']]},{title:'执行与治理',items:[['preparations','shield','准备与审批'],['runs','activity','运行中心'],['governance/audit','book','审计证据'],['governance/benchmarks','chart','Benchmark 评测'],['governance/evolution','spark','安全进化']]}]
const projectName=computed(()=>session.project?.name||'选择项目')
const root=computed(()=>`/projects/${pid.value}`)
const standalone=computed(()=>route.path==='/login')
watch(()=>route.fullPath,()=>{menu.value=false})
window.addEventListener('session-expired',()=>{clearSession();void router.replace('/login')})
async function exit(){try{await logout();await router.push('/login')}catch{logoutError.value='退出结果待核实，请重新登录检查会话。';await router.push('/login')}}
</script>
<template>
  <div v-if="apiMode==='mock'" class="preview-ribbon" role="status">交互预览 · 全部为模拟数据，不连接真实后端，也不会执行真实任务</div>
  <RouterView v-if="standalone"/>
  <div v-else class="app-layout">
    <button v-if="menu" class="nav-backdrop" aria-label="关闭导航" @click="menu=false"/>
    <aside class="sidebar" :class="{expanded:menu}">
      <RouterLink to="/projects" class="brand" aria-label="ETL Agent 项目入口"><span class="brand-mark"><i/><i/><i/><i/></span><span>ETL<span class="brand-light">Agent</span><small>DATA INTEGRATION</small></span></RouterLink>
      <RouterLink to="/projects?all=1" class="workspace-switch"><span class="workspace-avatar">{{projectName.slice(0,1)}}</span><span><small>当前工作空间</small><strong>{{projectName}}</strong></span><Icon name="down" :size="15"/></RouterLink>
      <nav aria-label="主导航"><section v-for="group in groups" :key="group.title"><h2>{{group.title}}</h2><RouterLink v-for="item in group.items" :key="item[0]" :to="pid?`${root}/${item[0]}`:'/projects'" :class="{active:route.path.includes(`/${item[0]}`)}"><Icon :name="item[1]" :size="19"/><span>{{item[2]}}</span><span v-if="item[0]==='preparations'" class="nav-dot"/></RouterLink></section></nav>
      <div class="sidebar-bottom"><RouterLink to="/health-status" :class="{active:route.path==='/health-status'}"><Icon name="activity" :size="18"/>组件健康<span class="tiny-dot"/></RouterLink><div class="sidebar-note"><span class="note-symbol">✧</span><strong>让每一次数据流转<br>都有据可循。</strong><p>生成 · 审查 · 受管执行</p><span class="note-line"/></div><div class="account"><span class="avatar">{{displayName.slice(0,1)}}</span><span><strong>{{displayName}}</strong><small>项目授权账户</small></span><button class="icon-button" aria-label="退出当前会话" @click="exit"><Icon name="logout" :size="17"/></button></div></div>
    </aside>
    <div class="workspace-main"><header class="topbar"><div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开全部导航" :aria-expanded="menu" @click="menu=!menu"><Icon name="menu"/></button><Icon name="layers" :size="17"/><span>{{projectName}}</span><span class="slash">/</span><strong>{{route.meta.title}}</strong></div><div class="topbar-right"><span class="context-chip"><span class="tiny-dot"/>项目隔离</span><span class="timezone">UTC+8</span><span class="avatar small">{{displayName.slice(0,1)}}</span></div></header>
    <main id="main" tabindex="-1"><p v-if="logoutError" class="feedback error">{{logoutError}}</p><RouterView :key="String(route.params.projectId||'global')+route.path"/></main>
    <footer class="app-footer"><span>ETL Agent <span class="muted">/ 数据集成控制台</span></span><span>审批先于执行，事实优先于推断。</span></footer></div>
  </div>
</template>
