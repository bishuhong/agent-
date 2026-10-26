import { computed, ref } from 'vue'
import { get,queryString } from '../api/client'
import type { Page } from '../api/types'
import { useQuery } from './useQuery'
export function useCursorList<T>(path:string,poll=0,enabled=true){
 const stack=ref<string[]>(['']),search=ref(''),draft=ref(''),cursor=computed(()=>stack.value.at(-1)||'')
 const q=useQuery(s=>enabled?get<Page<T>>(path+queryString({limit:20,cursor:cursor.value,query:search.value}),s):Promise.resolve({data:{items:[],has_more:false,next_cursor:null,request_id:''},etag:null,requestId:''}),[cursor,search],enabled?poll:0)
 function apply(){stack.value=[''];search.value=draft.value.trim();void q.refresh(true)}
 function next(){if(q.data.value?.has_more&&q.data.value.next_cursor)stack.value.push(q.data.value.next_cursor)}
 function previous(){if(stack.value.length>1)stack.value.pop()}
 return {q,draft,search,stack,apply,next,previous}
}
