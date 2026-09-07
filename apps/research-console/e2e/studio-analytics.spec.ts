import AxeBuilder from '@axe-core/playwright';
import {expect,test} from '@playwright/test';
import {installStudioApi} from './studio-fixtures';
for (const path of ['/studio/market', '/studio/analysis']) {
 for (const theme of ['light', 'dark']) {
  test(`${path} ${theme} charts support inspection and responsive accessible layouts`, async ({page}) => {
   await installStudioApi(page);
   await page.goto(path);
   await expect(page.getByRole('img').first()).toBeVisible();
   if (theme === 'dark') await page.getByRole('button', {name: '切换深色模式'}).click();
   const palette=await page.locator('.s-plot svg').first().evaluate(svg=>({
    plot:getComputedStyle(svg.querySelector('rect')!).fill,
    panel:getComputedStyle(svg.closest('.s-panel')!).backgroundColor,
   }));
   expect(palette.plot).toBe(palette.panel);
   for (const width of [1440, 768, 390]) {
    await page.setViewportSize({width, height: 1000});
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    const audit = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(audit.violations).toEqual([]);
   }
   if (path === '/studio/analysis') {
    await page.getByRole('button', {name: '放大技术权益', exact: true}).click();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', {name: '放大技术权益', exact: true})).toBeVisible();
    await page.getByRole('button', {name: '数据表', exact: true}).first().click();
    await expect(page.getByRole('region', {name: '技术权益数据表', exact: true})).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', {name: 'CSV', exact: true}).first().click();
    expect((await download).suggestedFilename()).toBe('技术权益.csv');
   }
  });
 }
}
test('continuous observation status remains readable in dark mode',async({page})=>{
 await installStudioApi(page);
 await page.route('**/api/v1/console/forward-dashboard',route=>route.fulfill({json:{source:'BACKEND_COMPUTED_SNAPSHOT',read_only:true,api_version:'0.1.0',data_provenance:'E2E_FIXED_RESEARCH_SNAPSHOT',authoritative:false,observations:[],errors:[],as_of_ms:1788768060000,schedule:{status:'OBSERVING',fresh:true,observed_at_ms:1788768060000,attempts:[]}}}));
 await page.goto('/studio/forward');
 await page.getByRole('button',{name:'切换深色模式'}).click();
 await expect(page.getByText('观察中',{exact:true})).toBeVisible();
 const audit=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
 expect(audit.violations).toEqual([]);
});
