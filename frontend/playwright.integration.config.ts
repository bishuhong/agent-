import { defineConfig } from '@playwright/test'
export default defineConfig({testDir:'tests/e2e-real',timeout:45000,workers:1,use:{baseURL:'http://127.0.0.1:5175',channel:'chrome',headless:true,trace:'retain-on-failure'},webServer:{command:'npm run dev -- --mode integration --port 5175',url:'http://127.0.0.1:5175',reuseExistingServer:true},reporter:'list'})
