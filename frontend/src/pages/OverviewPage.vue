<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { get } from '../api/client'
import type { Model, Page, Pipeline } from '../api/types'
import { session, allowed } from '../composables/session'
import { useQuery } from '../composables/useQuery'
import { count, date, percentage, barPercent } from '../api/format'
import Icon from '../components/Icon.vue'
import PageHeader from '../components/PageHeader.vue'
import Feedback from '../components/Feedback.vue'
import ResourceTable from '../components/ResourceTable.vue'
import StatusBadge from '../components/StatusBadge.vue'
const router=useRouter(),root=`/projects/${session.project!.id}`
const overview=useQuery(s=>get<Model<'ProjectOverviewResponse'>>(`/api/v1${root}/overview`,s))
const pipelines=useQuery(s=>get<Page<Pipeline>>(`/api/v1${root}/pipelines?limit=5`,s))
const health=useQuery(s=>get<Model<'HealthResult'>>('/health',s))
const stats=computed(()=>[{title:'数据连接',icon:'database',value:count(overview.data.value?.connection_count),foot:'项目内受管数据源',link:'assets'},{title:'数据流水线',icon:'flow',value:count(overview.data.value?.pipeline_count),foot:'从设计到可靠执行',link:'pipelines'},{title:'待审批门禁',icon:'shield',value:count(overview.data.value?.pending_approval_count),foot:'独立审批守住边界',link:'preparations'},{title:'运行成功率',icon:'chart',value:percentage(overview.data.value?.run_success_rate),foot:'最近 7 天 · 已终止运行',link:'runs'}])
const columns=[{key:'name',label:'流水线名称'},{key:'code',label:'标识'},{key:'status',label:'设计状态',kind:'status'},{key:'updated_at',label:'最近更新',kind:'date'}]
const resources=computed(()=>overview.data.value?.resource_distribution||[])
const model=import.meta.env.VITE_MODEL_NAME || 'deepseek-v4.1-flash'
</script>
<template><PageHeader eyebrow="YOUR DATA, CONNECTED." title="总览工作台" description="把数据连接起来，让每一条流水线清晰可控。"><button class="button" @click="overview.refresh();pipelines.refresh();health.refresh()"><Icon name="refresh" :size="15"/>刷新概览</button><RouterLink v-if="allowed('create_pipeline')" :to="`${root}/pipelines?create=1`" class="button primary"><Icon name="plus" :size="16"/>新建流水线</RouterLink></PageHeader><Feedback :error="overview.error.value" :loading="overview.loading.value" @retry="overview.refresh()"/>
<div class="stats-grid"><RouterLink v-for="stat in stats" :key="stat.title" :to="`${root}/${stat.link}`" class="card stat-card"><div class="stat-top"><span>{{stat.title}}</span><span class="stat-icon"><Icon :name="stat.icon" :size="16"/></span></div><div class="stat-number">{{stat.value}}</div><div class="stat-bottom"><span>{{stat.foot}}</span><span>↗</span></div></RouterLink></div>
<div class="workspace-grid"><div class="stack"><section class="card hero-card"><div><div class="eyebrow">DESIGN WITH INTELLIGENCE</div><h2>用自然语言，<br>开启你的下一条数据流水线。</h2><p>描述需求，生成可审查的集成方案。<br>每一步都有校验，每一次执行都有依据。</p><RouterLink :to="`${root}/pipelines`" class="button small ghost">进入 Pipeline Studio <Icon name="arrow" :size="14"/></RouterLink></div><div class="hero-visual" aria-hidden="true"><div class="hero-ring"/><div class="hero-cube"><Icon name="flow" :size="30"/></div><span class="hero-node one">✧</span><span class="hero-node two">↗</span><span class="hero-node three">✓</span></div></section>
<section class="card"><div class="card-heading"><div><h2>最近的流水线</h2><p>持续完善设计，将想法转化为可执行方案。</p></div><RouterLink :to="`${root}/pipelines`" class="link-arrow">查看全部 <Icon name="arrow" :size="13"/></RouterLink></div><Feedback :error="pipelines.error.value" :loading="pipelines.loading.value"/><ResourceTable v-if="pipelines.data.value" :rows="pipelines.data.value.items" :columns="columns" empty="从你的第一条流水线开始" @open="router.push(`${root}/pipelines?open=${$event.id}`)"/></section>
<section class="card"><div class="card-heading"><div><h2>从需求到可信交付</h2><p>完整链路，始终在你的掌握之中。</p></div><span class="panel-kicker">THE WORKFLOW</span></div><div class="workflow"><div v-for="(step,i) in [['spark','智能设计','描述需求，生成方案'],['code','确定性校验','审查映射与质量规则'],['shield','独立审批','冻结事实，职责分离'],['play','受管执行','监控运行与发布结果']]" :key="i" class="workflow-step"><span class="step-icon"><Icon :name="step[0]" :size="17"/></span><strong>0{{i+1}} · {{step[1]}}</strong><p>{{step[2]}}</p></div></div></section></div>
<div class="stack"><section class="card"><div class="card-heading"><h2>数据资源分布</h2><RouterLink :to="`${root}/assets`" class="link-arrow"><Icon name="arrow" :size="15"/></RouterLink></div><div class="card-body"><div v-for="resource in resources" :key="resource.connector_type" class="resource-row"><span class="resource-type">{{resource.connector_type.slice(0,2).toUpperCase()}}</span><div><strong>{{resource.connector_type}}</strong><div class="resource-bar"><i :style="{width:`${barPercent(resource.resource_count,overview.data.value?.connection_count||'0')}%`}"/></div></div><span class="subtle-label">{{count(resource.resource_count)}} 个</span></div><p v-if="!resources.length" class="muted" style="font-size:11px;padding:12px 0">尚无已确认的数据源。连接后查看资源分布。</p></div></section>
<section class="card"><div class="card-heading"><h2>环境就绪状态</h2><Icon name="activity" :size="17"/></div><div class="card-divider health-summary"><span class="health-orb"><Icon name="shield" :size="19"/></span><div><StatusBadge :value="health.data.value?.status||'unknown'"/><p>{{date(health.data.value?.checked_at)}} · UTC+8</p></div></div><div class="card-body"><Feedback :error="health.error.value"/><RouterLink to="/health-status" class="link-arrow">检查组件与依赖 <Icon name="arrow" :size="13"/></RouterLink></div></section>
<section class="card model-card"><span class="model-symbol">✧</span><div class="eyebrow">YOUR AI CO-PILOT</div><strong>{{model}}</strong><p>从字段映射到质量契约，<br>让 AI 辅助设计，让规则约束执行。</p><div class="model-meta"><span>DeepSeek · 受管模型</span><Icon name="lock" :size="12"/></div></section></div></div></template>
