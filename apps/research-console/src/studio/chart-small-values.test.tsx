import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {Chart} from './Charts';

it.each([0.0099,0.0000000000099])('keeps five distinct numeric ticks for small cost range %s',maximum=>{
 render(<Chart title="Small cost" rows={[{label:'A',fee:maximum/10},{label:'B',fee:maximum}]}
  series={[{key:'fee',label:'Fee'}]} kind="bar" unit="USDC"/>);
 const labels=Array.from(screen.getByRole('img').querySelectorAll('text')).map(n=>n.textContent??'')
  .filter(text=>!['A','B'].includes(text));
 expect(labels).toHaveLength(5);
 expect(new Set(labels).size).toBe(5);
 expect(labels).not.toContain('-0.00');
 expect(labels.filter(text=>Number(text.replace(/,/g,''))>0).length).toBeGreaterThan(2);
});
