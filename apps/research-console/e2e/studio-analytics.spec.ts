import AxeBuilder from '@axe-core/playwright';
import {expect,test} from '@playwright/test';
import {installStudioApi} from './studio-fixtures';
test('market and analytics charts support inspection and responsive accessible layouts',async({page})=>{
 await installStudioApi(page);
 for(const path of ['/studio/market','/studio/analysis']){
  await page.goto(path);
  await expect(page.getByRole('img').first()).toBeVisible();
  for(const width of [1440,768,390]){
   await page.setViewportSize({width,height:1000});
   await page.evaluate(()=>document.fonts.ready);
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBe(true);
   const audit=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
   expect(audit.violations).toEqual([]);
  }
 }
 await page.getByRole('button',{name:'放大技术权益',exact:true}).click();
 await page.keyboard.press('Escape');
 await expect(page.getByRole('button',{name:'放大技术权益',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'数据表',exact:true}).first().click();
 await expect(page.getByRole('region',{name:'技术权益数据表',exact:true})).toBeVisible();
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'CSV',exact:true}).first().click();
 expect((await download).suggestedFilename()).toBe('技术权益.csv');
});
