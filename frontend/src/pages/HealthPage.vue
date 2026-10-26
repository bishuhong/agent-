<script setup lang="ts">
import { get } from '../api/client'
import type { Model } from '../api/types'
import { useQuery } from '../composables/useQuery'
import PageHeader from '../components/PageHeader.vue'
import Feedback from '../components/Feedback.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Icon from '../components/Icon.vue'
import { date } from '../api/format'
const q=useQuery(s=>get<Model<'HealthResult'>>('/health',s))
</script>
<template><PageHeader eyebrow="SYSTEM READINESS" title="组件健康" description="区分服务可访问与依赖已就绪，不把未知当成正常。"><button class="button" @click="q.refresh()"><Icon name="refresh" :size="15"/>检查就绪状态</button></PageHeader><Feedback :error="q.error.value" :loading="q.loading.value" @retry="q.refresh()"/><section v-if="q.data.value" class="card"><div class="card-heading"><div><h2>系统总体状态</h2><p>{{date(q.data.value.checked_at)}} · Asia/Shanghai</p></div><StatusBadge :value="q.data.value.status"/></div><div class="card-body"><p class="form-info">部分依赖不可用不代表所有历史数据无法读取。真实执行、发布和外部模型的就绪性仍由后端按实际能力判断。</p><p v-if="!q.data.value.components.length" class="block-note">当前接口仅提供安全的总体投影，没有下发组件详情，不推测各组件已就绪。</p></div></section><div v-if="q.data.value?.components.length" class="health-grid section-space"><article v-for="c in q.data.value.components" :key="c.name" class="card"><div class="actions" style="justify-content:space-between"><h2>{{c.name}}</h2><StatusBadge :value="c.status"/></div><p>{{c.message||'服务端已确认该组件的就绪结果。'}}</p></article></div></template>
