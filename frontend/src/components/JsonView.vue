<script setup lang="ts">import { computed } from 'vue';const props=defineProps<{value:unknown;code?:boolean}>();const text=computed(()=>props.value===null||props.value===undefined?'尚未产生':typeof props.value==='string'?props.value:JSON.stringify(props.value,null,2))</script>
<template><pre class="code-view" :class="{light:!code}" tabindex="0">{{text}}</pre></template>
