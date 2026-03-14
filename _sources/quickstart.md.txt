# 快速入门

## 运行内置示例

最快的体验方式是直接运行内置示例：

```bash
evas run clk_div
```

该命令会：
1. 将 `clk_div` 的 Verilog-A 模型和 Spectre 测试台复制到 `./clk_div/`
2. 仿真该网表，输出结果保存到 `./output/clk_div/`
3. 运行分析脚本，生成 `analyze_clk_div.png` 波形图

## 仿真自定义网表

```bash
evas simulate path/to/tb.scs -o output/mydesign
```

每次仿真产生如下输出文件：

| 文件 | 内容 |
|------|------|
| `tran.csv` | 时域波形数据 |
| `tran.png` | 自动生成的多面板波形图 |
| `strobe.txt` | 按时间排序的所有 `$strobe` 日志行 |

## CSV 输出格式

信号默认以 6 位科学计数法（`:6e`）输出。
`save` 语句支持对每个信号单独指定格式后缀：

```
save vin:10e vout:6e clk:2e dout_code:d
```

| 后缀 | 格式 | 示例 |
|------|------|------|
| `:6e` | `:.6e`（默认） | `4.500000e-01` |
| `:10e` | `:.10e` | `4.5000000000e-01` |
| `:2e` | `:.2e` | `4.50e-01` |
| `:4f` | `:.4f` | `0.4500` |
| `:d` | 整数 | `7` |

## 支持的 Verilog-A 语法特性

- `@(cross(...))、@(above(...))` 过零事件
- `@(initial_step)` 初始化
- `transition()` 算子（支持延迟、上升/下降时间）
- `V(node) <+` 电压贡献
- 算术、逻辑、位运算、移位、三元运算符
- `for` 循环、`if/else`、`begin/end` 块
- 整型/实型变量、数组、带范围的参数
- `` `include``、`` `define``、`` `default_transition `` 预处理指令
- SI 单位后缀；数学函数：`ln`、`log`、`exp`、`sqrt`、`pow`、`abs`、`sin`、`cos`、`floor`、`ceil`、`min`、`max`
