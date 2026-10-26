<script setup lang="ts">
import { ref,onMounted } from 'vue'
import { useRoute,useRouter } from 'vue-router'
import { get,post } from '../api/client'
import type { Page,Pipeline,Version,Model } from '../api/types'
import { session,allowed } from '../composables/session'
import { useCursorList } from '../composables/useCursorList'
import { useMutation } from '../composables/useMutation'
import { useDirty } from '../composables/useDirty'
import PageHeader from '../components/PageHeader.vue'
import Feedback from '../components/Feedback.vue'
import ResourceTable from '../components/ResourceTable.vue'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
const root=`/projects/${session.project!.id}`,list=useCursorList<Pipeline>(`/api/v1${root}/pipelines`),op=useMutation(),dirty=useDirty(),route=useRoute(),router=useRouter()
const creating=ref(route.query.create==='1'),name=ref(''),code=ref(''),description=ref('')
async function create(){list.q.invalidate();const r=await op.run(()=>post<Model<'PipelineCreateResponse'>>('/api/v1/pipelines',{project_id:session.project!.id,name:name.value.trim(),code:code.value.trim(),description:description.value.trim()||null}),'流水线已创建。');if(r){dirty.value=false;creating.value=false;await router.push(`${root}/pipelines/${r.data.pipeline.id}/studio/${r.data.candidate_version_id}`)}}
async function open(row:Record<string,unknown>){const id=String(row.id);const r=await op.run(()=>get<Page<Version>>(`/api/v1/pipelines/${id}/versions?limit=1`),'');if(r?.data.items[0])await router.push(`${root}/pipelines/${id}/studio/${r.data.items[0].id}`)}
onMounted(()=>{if(typeof route.query.open==='string')void open({id:route.query.open})})
</script>
<template><PageHeader eyebrow="BUILD YOUR DATA FLOWS" title="数据流水线" description="从自然语言需求出发，设计、校验并冻结可审查的数据集成方案。"><button class="button primary" :disabled="!allowed('create_pipeline')" @click="creating=true"><Icon name="plus" :size="16"/>新建流水线</button></PageHeader><div class="toolbar"><form class="search-box" @submit.prevent="list.apply()"><Icon name="search" :size="16"/><input v-model="list.draft.value" aria-label="按流水线名称搜索" placeholder="搜索流水线名称…"/><button class="text-button">查询</button></form><span class="subtle-label">按创建时间排序 · 项目内资源</span></div><Feedback :error="list.q.error.value||op.error.value" :loading="list.q.loading.value" @retry="list.q.refresh()"/><section class="card"><ResourceTable v-if="list.q.data.value" :rows="list.q.data.value.items" :columns="[{key:'name',label:'流水线名称'},{key:'code',label:'流水线标识'},{key:'status',label:'设计状态',kind:'status'},{key:'updated_at',label:'最近更新',kind:'date'}]" @open="open"/><div class="pagination"><span>第 {{list.stack.value.length}} 段结果</span><div><button class="button small" :disabled="list.stack.value.length===1" @click="list.previous()">上一段</button><button class="button small" :disabled="!list.q.data.value?.has_more" @click="list.next()">下一段</button></div></div></section><Modal :open="creating" title="新建数据流水线" :busy="op.busy.value" @close="creating=false"><form @submit.prevent="create" @input="dirty=true"><div class="form-stack"><label class="field">流水线名称<input v-model="name" name="name" required placeholder="例如：订单数据归集"/></label><label class="field">项目内唯一标识<input v-model="code" name="code" required placeholder="例如：orders_integration"/></label><label class="field">描述（可选）<textarea v-model="description" name="description" placeholder="这条流水线将解决什么问题？"/></label></div><Feedback :error="op.error.value" :unknown="op.unknown.value"/><div class="form-actions"><button type="button" class="button" @click="creating=false">取消</button><button class="button primary" :disabled="op.busy.value||op.unknown.value">创建并进入 Studio <Icon name="arrow" :size="15"/></button></div></form></Modal></template>
