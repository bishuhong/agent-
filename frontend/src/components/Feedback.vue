<script setup lang="ts">
import { ApiError } from '../api/client'
import { isRecord } from '../api/types'
defineProps<{error?:Error|null;notice?:string;loading?:boolean;unknown?:boolean}>()
defineEmits<{retry:[]}>()
const fieldText=(field:unknown)=>isRecord(field)?`${String(field.path||'输入')}：${String(field.message||'取值无效')}`:'字段取值无效'
function focusField(field:unknown){if(!isRecord(field)||typeof field.path!=='string')return;const last=field.path.split('.').at(-1)||'';const aliases:Record<string,string>={source_profile_ids:'source',target_profile_id:'target',max_input_records:'maxInput',max_output_bytes:'maxBytes',max_duration_ms:'duration',max_amplification_ratio:'amplification',max_rejection_ratio:'rejection'};const name=aliases[last]||last;document.querySelector<HTMLElement>(`[name="${CSS.escape(name)}"]`)?.focus()}
</script>
<template><div v-if="error" class="feedback error" role="alert" tabindex="-1"><strong>{{unknown?'提交结果待核实':error.message}}</strong><p v-if="unknown">请核对原对象的服务端状态，当前不会自动重发请求。</p><small v-if="error instanceof ApiError">{{error.code}}<span v-if="error.requestId"> · 请求 {{error.requestId}}</span></small><ul v-if="error instanceof ApiError && error.fields.length"><li v-for="(field,index) in error.fields" :key="index"><button type="button" class="text-button" @click="focusField(field)">{{fieldText(field)}}</button></li></ul><button v-if="$attrs.onRetry" class="text-button" @click="$emit('retry')">重新查询</button></div><div v-else-if="notice" class="feedback success" role="status">{{notice}}</div><div v-if="loading" class="loading-line" role="status" aria-label="正在读取服务端数据"><span/></div></template>
