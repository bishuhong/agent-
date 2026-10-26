<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Model } from '../api/types'
import { session } from '../composables/session'
import { useCursorList } from '../composables/useCursorList'
import PageHeader from '../components/PageHeader.vue'
import Feedback from '../components/Feedback.vue'
import ResourceTable from '../components/ResourceTable.vue'
import Icon from '../components/Icon.vue'
const router=useRouter(),root=`/projects/${session.project!.id}`,list=useCursorList<Model<'ExecutionRunSummary'>>(`/api/v1${root}/execution-runs`)
</script>
<template><PageHeader eyebrow="OBSERVE EVERY EXECUTION" title="运行中心" description="执行、发布和回滚分别记录，受理不等于最终成功。"><button class="button" @click="list.q.refresh()"><Icon name="refresh" :size="15"/>刷新运行</button></PageHeader><Feedback :error="list.q.error.value" :loading="list.q.loading.value" @retry="list.q.refresh()"/><section class="card"><ResourceTable v-if="list.q.data.value" :rows="list.q.data.value.items" :columns="[{key:'id',label:'运行标识',kind:'id'},{key:'execution_status',label:'执行',kind:'status'},{key:'publication_status',label:'发布',kind:'status'},{key:'rollback_status',label:'回滚',kind:'status'},{key:'started_at',label:'开始时间',kind:'date'}]" @open="router.push(`${root}/runs/${$event.id}`)"/><div class="pagination"><span>第 {{list.stack.value.length}} 段 · 独立运行事实</span><div><button class="button small" :disabled="list.stack.value.length===1" @click="list.previous">上一段</button><button class="button small" :disabled="!list.q.data.value?.has_more" @click="list.next">下一段</button></div></div></section></template>
