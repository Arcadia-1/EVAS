# 示例目录

EVAS 内置 16 个示例电路，涵盖从基础数字逻辑到复杂混合信号系统的各类场景。

使用 `evas run <名称>` 运行任意示例。

## 示例一览

| 名称 | 描述 |
|------|------|
| `clk_div` | 时钟分频器（分频比 = 4） |
| `clk_burst_gen` | 时钟突发信号发生器 |
| `digital_basics` | 基础数字门（与、非、或、DFF、反相器链） |
| `lfsr` | 线性反馈移位寄存器（LFSR） |
| `noise_gen` | 噪声信号发生器 |
| `ramp_gen` | 斜坡信号发生器 |
| `edge_interval_timer` | 边沿间隔计时器 |
| `d2b_4b` | 4 位温度码转二进制码 |
| `dac_binary_clk_4b` | 4 位二进制 DAC（时钟驱动） |
| `dac_therm_16b` | 16 位温度码 DAC |
| `adc_dac_ideal_4b` | 4 位理想 ADC + DAC（含采样保持，3 种激励） |
| `cmp_strongarm` | StrongARM 比较器 |
| `cmp_offset_search` | 比较器失调二分搜索（已验证收敛至目标偏置） |
| `dwa_ptr_gen` | DWA 指针生成器 — 重叠版（100 MHz，`v2b_4b` 电压输入） |
| `dwa_ptr_gen_no_overlap` | DWA 指针生成器 — 无重叠版 |
| `sar_adc_dac_weighted_8b` | 8 位加权 SAR ADC + DAC |

## 运行多测试台示例

`adc_dac_ideal_4b` 和 `digital_basics` 包含多个测试台文件，
默认运行 `tb_<名称>.scs`，可用 `--tb` 指定其他测试台：

```bash
# adc_dac_ideal_4b 的三种激励
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_ramp.scs
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_sine.scs
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_sine1000.scs

# digital_basics 的各个门
evas run digital_basics --tb tb_and_gate.scs
evas run digital_basics --tb tb_not_gate.scs
evas run digital_basics --tb tb_or_gate.scs
evas run digital_basics --tb tb_dff_rst.scs
evas run digital_basics --tb tb_inverter_chain.scs
```

