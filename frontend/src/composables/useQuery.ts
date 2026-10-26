import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import type { WatchSource } from 'vue'
import type { Result } from '../api/types'
export function useQuery<T>(loader:(signal:AbortSignal)=>Promise<Result<T>>, deps:WatchSource[]=[], poll=0) {
  const data=shallowRef<T|null>(null), etag=ref<string|null>(null), error=shallowRef<Error|null>(null), loading=ref(true), updated=ref('')
  let seq=0, controller:AbortController|undefined, timer:ReturnType<typeof setTimeout>|undefined, disposed=false
  function schedule(){if(poll&&!disposed)timer=setTimeout(()=>{if(document.visibilityState==='visible')void refresh();else schedule()},poll)}
  async function refresh(clear=false) {
    const current=++seq; controller?.abort();clearTimeout(timer);controller=new AbortController()
    if(clear) data.value=null
    loading.value=true;error.value=null
    try { const result=await loader(controller.signal);if(current===seq&&!disposed){data.value=result.data;etag.value=result.etag;updated.value=new Date().toISOString()} }
    catch(e){if(current===seq&&!disposed && !(e instanceof DOMException&&e.name==='AbortError')) {error.value=e instanceof Error?e:new Error('请求失败'); if('status' in error.value && [401,403,404].includes(Number(error.value.status))) data.value=null} }
    finally {if(current===seq&&!disposed){loading.value=false;schedule()}}
  }
  function invalidate(){seq++;controller?.abort();clearTimeout(timer)}
  watch(deps,()=>void refresh(true),{immediate:true})
  onBeforeUnmount(()=>{disposed=true;invalidate()})
  return {data,etag,error,loading,updated,refresh,invalidate}
}
