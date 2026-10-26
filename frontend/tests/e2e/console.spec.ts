import { test,expect } from '@playwright/test'
import fixtures from '../../src/mocks/fixtures.json' with { type: 'json' }
const root=`/projects/${fixtures.project.id}`
test('桌面预览全部页面可达，模拟模式不发送真实 API 请求',async({page})=>{
 const errors:string[]=[],calls:string[]=[]
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/\/api\/v1\/|\/health$/.test(r.url()))calls.push(r.url())})
 await page.setViewportSize({width:1440,height:1000});await page.goto('/')
 await expect(page.getByRole('heading',{name:'总览工作台',exact:true})).toBeVisible()
 await expect(page.getByText('交互预览 · 全部为模拟数据，不连接真实后端，也不会执行真实任务')).toBeVisible()
 await page.screenshot({path:'../docs/frontend-desktop.png',fullPage:true})
 for(const [path,title] of [['assets','数据连接与资产'],['pipelines','数据流水线'],[`pipelines/${fixtures.pipelines[0]!.id}/studio/${fixtures.versions[0]!.id}`,'Pipeline Studio'],[`versions/${fixtures.versions[0]!.id}`,'版本审查与准备'],['preparations','准备与审批'],[`preparations/${fixtures.preparation.preparation.id}`,'准备单详情'],['runs','运行中心'],[`runs/${Object.keys(fixtures.runs)[0]}`,'运行详情'],['governance/audit','审计证据'],['governance/benchmarks','Benchmark 分层评测'],['governance/evolution','安全进化']]){
  await page.goto(`${root}/${path}`);await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible();await expect(page.locator('body')).not.toContainText('响应与约定契约不匹配')
 }
 await page.goto('/projects?all=1');await expect(page.getByRole('heading',{name:'项目与成员',exact:true})).toBeVisible()
 await page.goto('/health-status');await expect(page.getByRole('heading',{name:'组件健康',exact:true})).toBeVisible()
 expect(calls).toEqual([]);expect(errors).toEqual([])
})
test('手机导航完整，核心页面无横向溢出，冻结版无法重新生成',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/')
 await expect(page.getByRole('heading',{name:'总览工作台',exact:true})).toBeVisible()
 await page.screenshot({path:'../docs/frontend-mobile.png',fullPage:true})
 await page.getByRole('button',{name:'打开全部导航'}).click()
 await expect(page.getByRole('link',{name:'安全进化',exact:true})).toBeVisible()
 await page.getByRole('link',{name:'数据连接与资产',exact:true}).click()
 await expect(page.getByRole('heading',{name:'数据连接与资产',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'新建连接',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).not.toBeVisible()
 await page.goto(`${root}/pipelines/${fixtures.pipelines[0]!.id}/studio/${fixtures.versions[0]!.id}`)
 await expect(page.getByRole('button',{name:'生成集成方案',exact:true})).toBeDisabled()
 await page.getByRole('tab',{name:'数据流',exact:true}).click();await expect(page.getByText('orders_source',{exact:true})).toBeVisible()
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1)
 expect(overflow).toBe(false)
 await page.screenshot({path:'../docs/frontend-studio-mobile.png',fullPage:true})
})
test('创建候选和生成通过模拟传输，失败动作有明确反馈',async({page})=>{
 await page.goto(`${root}/pipelines`)
 await page.getByRole('button',{name:'新建流水线',exact:true}).click()
 await page.getByLabel('流水线名称',{exact:true}).fill('浏览器验证流水线')
 await page.getByLabel('项目内唯一标识',{exact:true}).fill('browser_contract_test')
 await page.getByRole('button',{name:'创建并进入 Studio'}).click()
 await expect(page.getByRole('heading',{name:'Pipeline Studio',exact:true})).toBeVisible()
 await page.locator('fieldset input[type=checkbox]').first().check()
 await page.getByRole('combobox',{name:'目标 Profile',exact:true}).selectOption(fixtures.profiles[1]!.id)
 await page.getByLabel('用自然语言描述需求',{exact:true}).fill('将订单字段映射到分析库，并检查订单编号非空。')
 await page.getByRole('button',{name:'生成集成方案',exact:true}).click()
 await expect(page.getByRole('heading',{name:'Agent 工作流',exact:true})).toBeVisible()
 await expect(page.getByRole('heading',{name:'确定性门禁',exact:true})).toBeVisible()
 await page.getByRole('tab',{name:'HOCON',exact:true}).click();await expect(page.locator('.code-view')).toContainText('parallelism')
})
