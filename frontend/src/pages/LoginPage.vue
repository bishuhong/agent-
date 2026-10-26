<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { post, setCsrf } from '../api/client'
import type { Model } from '../api/types'
import { restoreSession } from '../composables/session'
import { useMutation } from '../composables/useMutation'
import Feedback from '../components/Feedback.vue'
import Icon from '../components/Icon.vue'
const username=ref(''),password=ref(''),router=useRouter(),route=useRoute(),op=useMutation()
async function login(){const response=await op.run(()=>post<Model<'LoginResponse'>>('/api/v1/auth/login',{username:username.value.toLowerCase(),password:password.value}),'身份已确认。');password.value='';if(response){setCsrf(response.data.csrf_token);try{await restoreSession();const target=route.query.return;await router.replace(typeof target==='string'&&target.startsWith('/projects')&&!target.startsWith('//')?target:'/projects')}catch(e){op.error.value=e instanceof Error?e:new Error('身份恢复失败')}}}
</script>
<template><div class="login-page"><section class="login-art"><div class="brand"><span class="brand-mark"><i/><i/><i/><i/></span><span>ETL<span class="brand-light">Agent</span><small>DATA INTEGRATION</small></span></div><div><div class="eyebrow">FROM INTENT TO TRUSTED DATA</div><h1>从一个想法，<br>到一条<em>可信的数据流。</em></h1><p>让 AI 理解需求，让规则守住边界。<br>在一个工作空间里，完成数据集成的每一步。</p><div class="login-constellation"><div class="hero-visual"><div class="hero-ring"/><div class="hero-cube"><Icon name="flow" :size="29"/></div></div></div></div><footer>DESIGN · REVIEW · EXECUTE</footer></section><section class="login-form-area"><form class="login-form" @submit.prevent="login"><div class="eyebrow">WELCOME TO YOUR WORKSPACE</div><h1>欢迎回来</h1><p>登录预置账户，进入你的数据工作空间。</p><Feedback :error="op.error.value" :unknown="op.unknown.value"/><div class="form-stack"><label class="field">账号<input v-model="username" name="username" required autocomplete="username" pattern="[A-Za-z0-9_-]{3,64}" minlength="3" maxlength="64" placeholder="输入你的账号"/></label><label class="field">密码<input v-model="password" name="password" required type="password" autocomplete="current-password" minlength="12" maxlength="128" placeholder="输入你的密码"/></label><button class="button primary" :disabled="op.busy.value||op.unknown.value">{{op.busy.value?'正在确认身份…':'进入工作空间'}}<Icon name="arrow" :size="16"/></button></div><p class="login-footnote"><Icon name="lock" :size="12"/> 服务端会话保护 · 项目级访问隔离<br>账号由项目负责人预置，不开放公开注册。</p><RouterLink to="/health-status" class="link-arrow">查看服务健康 <Icon name="arrow" :size="13"/></RouterLink></form></section></div></template>
