<script setup lang="ts">import Icon from './Icon.vue';withDefaults(defineProps<{title?:string;description?:string;icon?:string}>(),{title:'这里还没有数据',description:'完成上一步后，已确认的业务记录会显示在这里。',icon:'layers'})</script>
<template><div class="empty-state"><span class="empty-icon"><Icon :name="icon" :size="28"/></span><h3>{{title}}</h3><p>{{description}}</p><slot/></div></template>
