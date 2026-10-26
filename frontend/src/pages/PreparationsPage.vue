<script setup lang="ts">
import { useRouter } from 'vue-router'
import { session } from '../composables/session'
import { useCursorList } from '../composables/useCursorList'
import type { Model } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import Feedback from '../components/Feedback.vue'
import ResourceTable from '../components/ResourceTable.vue'
import Icon from '../components/Icon.vue'
const router=useRouter(),root=`/projects/${session.project!.id}`,list=useCursorList<Model<'PreparationSummary'>>(`/api/v1${root}/preparations`)
</script>
<template><PageHeader eyebrow="INDEPENDENT REVIEW" title="准备与审批" description="审查被冻结的执行事实，数据与安全职责各自独立。"><button class="button" @click="list.q.refresh()"><Icon name="refresh" :size="15"/>刷新准备单</button></PageHeader><div class="block-note">申请人不能自批，同一自然人不能占用两个必需职责槽。服务端与 Worker 会在关键步骤重新核验。</div><Feedback :error="list.q.error.value" :loading="list.q.loading.value" @retry="list.q.refresh()"/><section class="card"><ResourceTable v-if="list.q.data.value" :rows="list.q.data.value.items" :columns="[{key:'id',label:'准备单',kind:'id'},{key:'approval_status',label:'审批状态',kind:'status'},{key:'validity',label:'事实有效性',kind:'status'},{key:'expires_at',label:'过期时间',kind:'date'}]" @open="router.push(`${root}/preparations/${$event.id}`)"/><div class="pagination"><span>第 {{list.stack.value.length}} 段 · 新记录在前</span><div><button class="button small" :disabled="list.stack.value.length===1" @click="list.previous">上一段</button><button class="button small" :disabled="!list.q.data.value?.has_more" @click="list.next">下一段</button></div></div></section></template>
