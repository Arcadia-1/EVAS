# 示例目录

EVAS 内置 **14 组示例**，每组包含一个或多个 Verilog-A 模型文件（`.va`）、
Spectre 格式的测试台 netlist（`.scs`），以及 Python 数据处理与可视化脚本。

使用 `evas run <名称>` 运行任意示例。

## 示例一览

| 组名 | 子示例 / 变体 |
|------|--------------|
| `clk_div` | 时钟分频器（分频比 = 4） |
| `clk_burst_gen` | 时钟突发信号发生器 |
| `digital_basics` | 与门、或门、非门；带复位 D 触发器；反相器链 |
| `lfsr` | 线性反馈移位寄存器 |
| `noise_gen` | 高斯噪声发生器 |
| `ramp_gen` | 斜坡信号发生器 |
| `edge_interval_timer` | 边沿间隔计时器 |
| `d2b_4b` | 4 位温度码转二进制码 |
| `dac_binary_clk_4b` | 4 位二进制 DAC（时钟驱动） |
| `dac_therm_16b` | 16 位温度码 DAC |
| `adc_dac_ideal_4b` | 4 位理想 ADC + DAC（含采样保持）：a) 斜坡  b) 单音正弦  c) 1000 点正弦 |
| `comparator` | a) 理想比较器  b) StrongARM 时钟比较器  c) 二分失调校准  d) 传播延迟测量 |
| `dwa_ptr_gen` | a) 重叠版（每周期 code+1 个单元）  b) 无重叠版 — 均以 `v2b_4b` 电压转数字 ADC 驱动，100 MHz 时钟 |
| `sar_adc_dac_weighted_8b` | 8 位二进制权重 SAR ADC + DAC；斜坡输入；DNL/INL 测试 |

## 运行特定子示例

当一个组包含多个测试台时，用 `--tb` 选择：

```bash
# adc_dac_ideal_4b 的三种激励
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_ramp.scs
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_sine.scs

# comparator 各子示例
evas run comparator --tb tb_cmp_ideal.scs
evas run comparator --tb tb_cmp_strongarm.scs
evas run comparator --tb tb_cmp_offset_search.scs
evas run comparator --tb tb_cmp_delay.scs

# digital_basics 各门
evas run digital_basics --tb tb_and_gate.scs
evas run digital_basics --tb tb_not_gate.scs
```


