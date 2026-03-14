# CLI 参考

EVAS 提供三个子命令：

## `evas list`

列出所有内置示例名称。

```bash
evas list
```

## `evas run <名称>`

将指定内置示例复制到当前目录并运行仿真。

```bash
evas run clk_div
evas run digital_basics
evas run sar_adc_dac_weighted_8b
```

对于包含多个测试台的示例（如 `adc_dac_ideal_4b`、`digital_basics`），
默认运行 `tb_<名称>.scs`，可用 `--tb` 指定其他测试台：

```bash
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_sine.scs
evas run digital_basics --tb tb_not_gate.scs
```

输出保存至 `./output/<名称>/`。若目录内存在 `analyze_<名称>.py` 分析脚本，
则在仿真完成后自动执行（通过 `EVAS_OUTPUT_DIR` 环境变量定位输出目录）。

## `evas simulate <file.scs>`

直接仿真任意 Spectre 网表文件。

```bash
evas simulate path/to/tb_mydesign.scs -o output/mydesign -log sim.log
```

| 选项 | 默认值 | 描述 |
|------|--------|------|
| `-o / --output` | `./output` | 输出目录 |
| `-log` | *（无）* | 日志文件路径 |

成功返回退出码 `0`，仿真出错返回 `1`。
