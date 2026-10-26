<script setup lang="ts">
import { isRecord } from '../api/types'
import { date,label,short } from '../api/format'
import StatusBadge from './StatusBadge.vue'
import EmptyState from './EmptyState.vue'
defineProps<{rows:unknown[];columns:{key:string;label:string;kind?:string}[];empty?:string}>();defineEmits<{open:[row:Record<string,unknown>]}>()
const record=(v:unknown)=>isRecord(v)?v:{}
const cell=(r:unknown,key:string)=>record(r)[key]
</script>
<template><div v-if="rows.length" class="table-scroll"><table><thead><tr><th v-for="col in columns" :key="col.key">{{col.label}}</th><th class="row-link-heading"><span class="sr-only">查看详情</span></th></tr></thead><tbody><tr v-for="(row,index) in rows" :key="String(cell(row,'id')||index)"><td v-for="(col,i) in columns" :key="col.key" :data-label="col.label"><StatusBadge v-if="col.kind==='status'" :value="cell(row,col.key)"/><span v-else-if="col.kind==='date'" class="muted">{{date(cell(row,col.key))}}</span><code v-else-if="col.kind==='id'">{{short(cell(row,col.key),12)}}</code><button v-else-if="i===0" class="row-title" @click="$emit('open',record(row))">{{cell(row,col.key)??'未命名'}}</button><span v-else>{{label(cell(row,col.key))}}</span></td><td><button class="row-arrow" aria-label="查看详情" @click="$emit('open',record(row))">↗</button></td></tr></tbody></table></div><EmptyState v-else :title="empty||'暂无记录'" description="此处仅展示服务端已确认的数据。"/></template>
