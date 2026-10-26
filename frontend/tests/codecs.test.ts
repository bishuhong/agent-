import { describe,it,expect } from 'vitest'
import { count,percentage,integerText,ratioText,localToInstant,barPercent } from '../src/api/format'
describe('精确值与时间契约',()=>{
 it('大整数保持精确，null 不当作零',()=>{expect(count('9007199254740993')).toBe('9,007,199,254,740,993');expect(count(null)).toBe('—');expect(count(0)).toBe('—')})
 it('百分比只做展示，不修改原始精确字符串',()=>{expect(percentage('0.9999')).toBe('99.99%');expect(percentage('0')).toBe('0.0%');expect(percentage(null)).toBe('—')})
 it('计数拒绝指数、小数、负数和空文本',()=>{for(const value of ['1e6','1.2','-1','', '1,000'])expect(()=>integerText(value)).toThrow();expect(integerText('9007199254740993')).toBe('9007199254740993')})
 it('比例不接受越界与非十进制输入',()=>{for(const value of ['1.1','-0.1','NaN','1e-2'])expect(()=>ratioText(value)).toThrow();expect(ratioText('0.0100')).toBe('0.0100')})
 it('时区转换不依赖操作系统本地时区',()=>{expect(localToInstant('2027-01-01T08:00','Asia/Shanghai')).toBe('2027-01-01T00:00:00.000Z');expect(localToInstant('2027-01-01T08:00','Asia/Kathmandu')).toBe('2027-01-01T02:15:00.000Z')})
 it('拒绝夏令时不存在时刻和重复时刻',()=>{expect(()=>localToInstant('2027-03-14T02:30','America/New_York')).toThrow('不存在');expect(()=>localToInstant('2027-11-07T01:30','America/New_York')).toThrow('重复')})
 it('拒绝无效日期，不静默滚动到下个月',()=>{expect(()=>localToInstant('2027-02-31T12:00','Asia/Shanghai')).toThrow()})
 it('资源分布展示不先将业务计数变成 number',()=>{expect(barPercent('9007199254740993','18014398509481986')).toBe(50);expect(barPercent('2','0')).toBe(0)})
})

it('拒绝超过1但会被浮点舍入的比例',()=>{expect(()=>ratioText('1.000000000000000001')).toThrow();expect(ratioText('0.999999999999999999')).toBe('0.999999999999999999')})
