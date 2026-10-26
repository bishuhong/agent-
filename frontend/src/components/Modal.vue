<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from 'vue'
import Icon from './Icon.vue'
const props=defineProps<{open:boolean;title:string;busy?:boolean}>();const emit=defineEmits<{close:[]}>();const dialog=ref<HTMLDialogElement>();const id='dialog-'+crypto.randomUUID()
const sync=async()=>{await nextTick();if(props.open&&!dialog.value?.open)dialog.value?.showModal();else if(!props.open&&dialog.value?.open)dialog.value?.close()}
watch(()=>props.open,sync);onMounted(sync)
</script>
<template><dialog ref="dialog" :aria-labelledby="id" @cancel="busy?$event.preventDefault():emit('close')" @close="emit('close')"><div class="modal-heading"><div><div class="eyebrow">ETL AGENT / WORKSPACE</div><h2 :id="id">{{title}}</h2></div><button class="icon-button" :disabled="busy" aria-label="关闭对话框" @click="emit('close')"><Icon name="close"/></button></div><div class="modal-body"><slot/></div></dialog></template>
