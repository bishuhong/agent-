import { defineConfig } from '@playwright/test'
export default defineConfig({testDir:'tests/e2e',timeout:30000,workers:1,use:{baseURL:'http://127.0.0.1:5174',channel:'chrome',headless:true,trace:'retain-on-failure'},webServer:{command:'npm run dev -- --mode preview --port 5174',url:'http://127.0.0.1:5174',reuseExistingServer:true},reporter:'list'})
