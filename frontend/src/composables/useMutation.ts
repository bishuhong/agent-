import { nextTick,ref, shallowRef } from 'vue'
import { ApiError } from '../api/client'
export function useMutation(){
  const busy=ref(false),error=shallowRef<Error|null>(null),notice=ref(''),unknown=ref(false)
  async function run<T>(call:()=>Promise<T>,success='操作已受理，请以最新服务端状态为准。'):Promise<T|undefined>{
    if(busy.value||unknown.value)return
    busy.value=true;error.value=null;notice.value=''
    try {const result=await call();notice.value=success;return result}
    catch(e){error.value=e instanceof Error?e:new Error('操作失败');unknown.value=e instanceof ApiError&&e.unknownResult;await nextTick();document.querySelector<HTMLElement>('[role="alert"]')?.focus();return undefined}
    finally{busy.value=false}
  }
  return {busy,error,notice,unknown,run}
}
