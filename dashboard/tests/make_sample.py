#!/usr/bin/env python3
"""生成合成测试数据（离线用），不连接任何真实服务。

用途
----
1. 本机没有服务器时，给 pytest / 前端调样式提供一份「形状正确的」历史数据样本。
2. 需要演示「干旱 / 正常 / 过湿」三种状态时，用参数直接造出来。

⚠️ 重要：这不是真实数据，也不会被导进线上看板。
   线上看板只读上游 `irrigation-server` 和 `/home/ubuntu/irrigation_history.db`，
   本脚本生成的文件只写到本地 `tests/` 下，**不会**写任何数据库。

输出格式与上游 `history` 表的列一一对应（契约 §7）：
    timestamp,ch1_moisture,ch1_pump,ch2_moisture,ch2_pump,ch3_moisture,ch3_pump
- timestamp：本地时间 ISO 串（与上游一致，不带时区）
- *_moisture：0-100 的浮点数，1 位小数
- *_pump：0/1 整数

用法
----
    python3 tests/make_sample.py                       # 默认：3 天，5 分钟一条，写 tests/sample_history.csv
    python3 tests/make_sample.py --days 7 --out tests/sample_history.csv
    python3 tests/make_sample.py --interval 60 --rows 120 --scenario normal --quiet
    python3 tests/make_sample.py --scenario dry        # 三路含水量都在 30% 以下（触发 dry 告警）
    python3 tests/make_sample.py --scenario wet        # 都在 95% 以上（触发 wet 告警）
    python3 tests/make_sample.py --scenario saturated  # 恒为 80.0（复刻服务器上模拟器的真实状态）
    python3 tests/make_sample.py --csv-utf8bom         # 带 BOM（测 CSV 导出兼容性用）

退出码：0 成功；2 参数错误；1 写文件失败。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import random
import sys

# 与上游一致的列名（契约 §7）
COLUMNS = [
    "timestamp",
    "ch1_moisture", "ch1_pump",
    "ch2_moisture", "ch2_pump",
    "ch3_moisture", "ch3_pump",
]

# 场景定义：每个通道的含水量生成方式
#   base / amp / drift 三个数控制一条「慢变 + 日周期 + 随机噪声」的曲线
SCENARIOS = {
    # 服务器上的真实状态：模拟器饱和，三路恒为 80.0（契约 §7 明确说过这不是 bug）
    "saturated": {"base": (80.0, 80.0, 80.0), "amp": (0.0, 0.0, 0.0), "noise": 0.0,
                  "pump": (1, 1, 1)},
    # 正常：60% 上下缓慢波动
    "normal":    {"base": (62.0, 58.0, 66.0), "amp": (6.0, 5.0, 7.0), "noise": 0.8,
                  "pump": (0, 0, 0)},
    # 干旱：全部 < 30%，应触发 dry 告警
    "dry":       {"base": (22.0, 18.0, 26.0), "amp": (3.0, 2.0, 3.0), "noise": 0.5,
                  "pump": (1, 1, 1)},
    # 过湿：全部 > 95%，应触发 wet 告警
    "wet":       {"base": (97.0, 96.5, 98.0), "amp": (1.0, 0.8, 1.0), "noise": 0.2,
                  "pump": (0, 0, 0)},
}


def build_rows(scenario: str, rows: int, interval: int,
               start: dt.datetime | None = None,
               seed: int = 20261024) -> list[dict]:
    """按场景造 rows 行数据。返回 list[dict]（键为 COLUMNS 里的列名）。"""
    cfg = SCENARIOS[scenario]
    rng = random.Random(seed)
    if start is None:
        # 默认从「现在往前推 rows*interval 秒」开始，这样最后一行接近当前时间
        start = dt.datetime.now().replace(microsecond=0) - dt.timedelta(seconds=rows * interval)

    out: list[dict] = []
    for i in range(rows):
        ts = start + dt.timedelta(seconds=i * interval)
        row: dict = {"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S")}
        for ch in range(3):
            base = cfg["base"][ch]
            amp = cfg["amp"][ch]
            # 日周期：24 小时一个正弦波，让曲线看起来像真的
            phase = 2.0 * math.pi * (i * interval) / 86400.0 + ch * 0.7
            val = base + amp * math.sin(phase) + rng.uniform(-cfg["noise"], cfg["noise"])
            val = max(0.0, min(100.0, val))
            row[f"ch{ch + 1}_moisture"] = round(val, 1)
            pumped = cfg["pump"][ch]
            # 除 saturated 外，模拟偶尔停泵（saturated 要保持「泵常开」的真实状态）
            if pumped and scenario != "saturated" and rng.random() < 0.02:
                pumped = 0
            row[f"ch{ch + 1}_pump"] = int(pumped)
        out.append(row)
    return out


def write_csv(rows: list[dict], path: str, bom: bool = False) -> int:
    """写 CSV。bom=True 时写 UTF-8 BOM（测导出兼容性用）。返回写入字节数。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    # newline="" 是 csv 模块在 Windows 上避免多空行的标准写法
    encoding = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return os.path.getsize(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成合成灌溉历史数据（CSV，仅本地测试用，不写任何数据库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="场景可选：saturated（恒 80.0，服务器真实状态）/ normal / dry / wet",
    )
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "sample_history.csv"),
                    help="输出路径，默认 tests/sample_history.csv")
    ap.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS),
                    help="数据场景，默认 normal")
    ap.add_argument("--days", type=float, default=3.0,
                    help="覆盖多少天（默认 3 天；与 --rows 同时给时以 --rows 为准）")
    ap.add_argument("--interval", type=int, default=300,
                    help="采样间隔秒数（默认 300 = 5 分钟，与上游 history 表一致）")
    ap.add_argument("--rows", type=int, default=0,
                    help="直接指定行数（0 = 按 --days 和 --interval 算）")
    ap.add_argument("--seed", type=int, default=20261024, help="随机种子，便于复现")
    ap.add_argument("--csv-utf8bom", action="store_true",
                    help="输出带 UTF-8 BOM（测试 BOM 处理用）")
    ap.add_argument("--quiet", action="store_true", help="不打印摘要")
    args = ap.parse_args(argv)

    if args.interval <= 0:
        print("错误：--interval 必须 > 0", file=sys.stderr)
        return 2
    if args.days <= 0 and args.rows <= 0:
        print("错误：--days 与 --rows 至少一个为正数", file=sys.stderr)
        return 2

    rows_n = args.rows if args.rows > 0 else max(1, int(args.days * 86400 / args.interval))
    if rows_n > 200_000:
        print(f"错误：{rows_n} 行太多了（上限 200000）。用 --days 或 --interval 调小。", file=sys.stderr)
        return 2

    try:
        rows = build_rows(args.scenario, rows_n, args.interval, seed=args.seed)
        size = write_csv(rows, args.out, bom=args.csv_utf8bom)
    except OSError as exc:
        print(f"错误：写文件失败：{exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        moist = [r[f"ch{c}_moisture"] for r in rows for c in (1, 2, 3)]
        print(f"场景     : {args.scenario}")
        print(f"行数     : {rows_n}（间隔 {args.interval}s，约 {rows_n * args.interval / 86400:.2f} 天）")
        print(f"时间范围 : {rows[0]['timestamp']} ~ {rows[-1]['timestamp']}")
        print(f"含水量   : min={min(moist):.1f} max={max(moist):.1f} avg={sum(moist) / len(moist):.1f}")
        if args.scenario == "saturated":
            print("注意     : saturated 场景三路恒为 80.0 —— 这就是服务器上模拟器饱和的真实状态，")
            print("           不是 bug。看板照实显示，不做美化。")
        print(f"输出     : {args.out}（{size} 字节，{len(COLUMNS)} 列，"
              f"{'带' if args.csv_utf8bom else '无'} BOM）")
        print("说明     : 本文件仅供本机测试，未被导入任何数据库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
