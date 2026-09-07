// The former cockpit contract is replaced by the user's Research Studio rebuild.
// Git retains the old UI fixtures and Windows/Linux screenshots as historical material.
import AxeBuilder from '@axe-core/playwright';
import { expect,test } from '@playwright/test';
import { installStudioApi } from './studio-fixtures';
test.beforeEach(async({page})=>{
 await installStudioApi(page);await page.goto('/');
 await expect(page.getByRole('heading',{name:'研究工作台',exact:true})).toBeVisible();
 await expect(page.getByRole('img',{name:'权益变化'})).toBeVisible();
 await page.evaluate(()=>document.fonts.ready);
});
test('studio is accessible and never overflows desktop or mobile',async({page})=>{
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:1000});
  const geometry=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
  expect(geometry.document).toBeLessThanOrEqual(geometry.viewport);expect(geometry.body).toBeLessThanOrEqual(geometry.viewport);
  const results=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze();
  expect(results.violations,results.violations.map(v=>`${v.id}: ${v.help}`).join('\n')).toEqual([]);
 }
});
test('studio visual contract',async({page})=>{await expect(page).toHaveScreenshot('research-studio.png');});
test('system completeness bookmark opens the new structured evidence view',async({page})=>{
 await page.goto('/research/system-completeness');await expect(page).toHaveURL(/\/studio\/modules\/system-completeness$/);
 await expect(page.getByRole('heading',{name:'数据与模块',exact:true})).toBeVisible();await expect(page.getByText('NOT_TOP_TIER',{exact:true})).toBeVisible();
});
test('factory scenario selection switches policy and cost without changing the source run',async({page})=>{
 await page.goto('/research/runs/run-studio-fixture');await page.getByLabel('单边滑点').selectOption('10');await page.getByLabel('亏损150 USDC停机').uncheck();await page.getByRole('button',{name:'PRICE_FLOW',exact:true}).click();
 await expect(page.getByRole('heading',{name:'PRICE_FLOW · 连续技术权益'})).toBeVisible();await expect(page).toHaveURL(/run-studio-fixture$/);
});
