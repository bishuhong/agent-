<script setup lang="ts">
import { ref } from 'vue'
import { get,post } from '../api/client'
import type { Page,Model } from '../api/types'
import { session,allowed,selectProject } from '../composables/session'
import { useQuery } from '../composables/useQuery'
import { useMutation } from '../composables/useMutation'
import PageHeader from '../components/PageHeader.vue'
import Icon from '../components/Icon.vue'
import Feedback from '../components/Feedback.vue'
import ResourceTable from '../components/ResourceTable.vue'
import EmptyState from '../components/EmptyState.vue'
const selected=ref(''),user=ref(''),role=ref('maker'),slot=ref(''),op=useMutation()
const members=useQuery(s=>selected.value?get<Page<Model<'ProjectMembershipResponse'>>>(`/api/v1/projects/${selected.value}/memberships`,s):Promise.resolve({data:{items:[],has_more:false,next_cursor:null,request_id:''},etag:null,requestId:''}),[selected])
async function manage(id:string){await selectProject(id);selected.value=id}
async function grant(){if(!session.project)return;await op.run(()=>post(`/api/v1/projects/${selected.value}/memberships`,{user_id:user.value,...(slot.value?{role_slot:slot.value}:{role:role.value}),expected_revision:session.project!.revision}));await members.refresh();session.project=(await get<Model<'ProjectResponse'>>(`/api/v1/projects/${selected.value}`)).data}
</script>
<template><PageHeader eyebrow="YOUR WORKSPACES" title="项目与成员" description="项目是资源、权限和运行记录的隔离边界。"/><div class="project-grid"><article v-for="project in session.projects" :key="project.id" class="card project-card"><span class="workspace-avatar">{{project.name.slice(0,1)}}</span><h2>{{project.name}}</h2><p>{{project.description||'在这个工作空间管理数据集成的完整生命周期。'}}</p><div class="actions"><RouterLink :to="`/projects/${project.id}/overview`" class="button primary small">进入工作空间 <Icon name="arrow" :size="14"/></RouterLink><button class="button small" @click="manage(project.id)">查看成员</button></div></article></div><EmptyState v-if="!session.projects.length" title="当前账号暂无可访问项目" description="请由项目负责人预置成员资格。"/><section v-if="selected" class="card section-space"><div class="card-heading"><h2>项目成员</h2></div><Feedback :error="members.error.value" :loading="members.loading.value"/><ResourceTable :rows="members.data.value?.items||[]" :columns="[{key:'user_id',label:'用户标识',kind:'id'},{key:'role',label:'项目角色'},{key:'created_at',label:'授予时间',kind:'date'}]"/><div v-if="allowed('manage_project_members')" class="card-body"><div class="block-note">当前后端没有可授予用户目录，仅能使用由负责人提供的已有用户 UUID；不会新建或邀请账号。</div><form @submit.prevent="grant"><div class="form-grid"><label class="field">已有用户 UUID<input v-model="user" name="user" required pattern="[a-fA-F0-9-]{36}"/></label><label class="field">角色<select v-model="role" name="role"><option v-for="r in ['maker','operator','data_checker','security_checker','auditor']" :key="r">{{r}}</option></select></label><label class="field">职责槽（留空则授予角色）<select v-model="slot" name="slot"><option value="">不设置</option><option value="data">数据</option><option value="security">安全</option></select></label></div><Feedback :error="op.error.value" :notice="op.notice.value"/><div class="form-actions"><button class="button primary" :disabled="op.busy.value||op.unknown.value">授予已选资格</button></div></form></div></section></template>
