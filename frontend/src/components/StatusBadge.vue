<script setup lang="ts">
import { computed } from 'vue'
import { label } from '../api/format'
const props=defineProps<{value:unknown}>()
const green=['succeeded','passed','published','approved','ready','valid','within_budget','enabled','active']
const red=['failed','rejected','invalid','blocked','exceeded']
const yellow=['running','queued','pending','pending_approval','cancel_requested','publishing','reconciling','waiting_input','validating','repairing','stale','expired','degraded','not_ready','requested']
const known=[...green,...red,...yellow,'draft','cancelled','authorization_expired','not_published','revoked','unknown','submitted']
const tone=computed(()=>green.includes(String(props.value))?'good':red.includes(String(props.value))?'bad':yellow.includes(String(props.value))?'warn':'neutral')
</script>
<template><span class="badge" :class="tone"><i/>{{known.includes(String(value))?label(value):'未知状态'}}</span></template>
