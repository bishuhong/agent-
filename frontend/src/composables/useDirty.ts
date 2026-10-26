import { onBeforeUnmount, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
export function useDirty(){const dirty=ref(false);const before=(e:BeforeUnloadEvent)=>{if(dirty.value){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',before);onBeforeUnmount(()=>window.removeEventListener('beforeunload',before));onBeforeRouteLeave(()=>!dirty.value||window.confirm('有尚未提交的输入，确定离开并丢弃吗？'));return dirty}
