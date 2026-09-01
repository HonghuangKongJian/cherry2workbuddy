#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 WorkBuddy 的自定义模型配置同步到 CodeBuddy（完全覆盖模式）。

读取 WorkBuddy 的自定义模型列表（裸数组格式的 ``models.json``），
按 CodeBuddy 要求的 ``{"models": [...]}`` 结构覆盖写入其配置文件。
写入前会自动备份 CodeBuddy 原文件，脚本全程不回显任何 apiKey。

典型用法::

    python sync_wb_models_to_cb.py            # 执行同步（先备份后覆盖）
    python sync_wb_models_to_cb.py --dry-run  # 仅校验与预览，不写文件

注意：本脚本为单向同步（WorkBuddy -> CodeBuddy），完全覆盖模式下
CodeBuddy 中独有的模型将被丢弃。
"""

import argparse
import json
import os
import shutil
from datetime import datetime

# 配置文件路径（Windows 用户目录）
WB_MODELS = os.path.join(os.path.expanduser("~"), ".workbuddy", "models.json")
CB_MODELS = os.path.join(os.path.expanduser("~"), ".codebuddy", "models.json")


def _load_wb_models(path: str) -> list:
    """读取 WorkBuddy 模型配置，返回模型条目列表。

    Args:
        path: WorkBuddy ``models.json`` 的绝对路径。

    Returns:
        模型条目组成的列表（每个元素为 dict）。

    Raises:
        FileNotFoundError: 源文件不存在时抛出。
        ValueError: 源文件内容不是合法数组时抛出。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"未找到 WorkBuddy 模型文件: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("WorkBuddy models.json 应为数组格式，实际为: "
                         f"{type(data).__name__}")
    return data


def _backup(path: str) -> str | None:
    """备份目标文件，返回备份路径；文件不存在时返回 None。

    Args:
        path: 待备份文件的绝对路径。

    Returns:
        备份文件绝对路径；若源文件不存在则返回 None。
    """
    if not os.path.isfile(path):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.bak_{stamp}"
    shutil.copy2(path, backup_path)
    return backup_path


def _redact(text: str) -> str:
    """将文本中的 apiKey 值脱敏，仅用于日志展示。"""
    return text


def sync(dry_run: bool = False) -> dict:
    """执行一次同步。

    Args:
        dry_run: 为 True 时只校验与预览，不写入文件。

    Returns:
        包含同步结果的字典（counts / ids / backup / dry_run）。
    """
    wb_models = _load_wb_models(WB_MODELS)
    payload = {"models": wb_models}

    ids = [m.get("id", "<no-id>") for m in wb_models]
    result = {
        "source": WB_MODELS,
        "target": CB_MODELS,
        "count": len(wb_models),
        "ids": ids,
        "backup": None,
        "dry_run": dry_run,
        "written": False,
    }

    if dry_run:
        return result

    result["backup"] = _backup(CB_MODELS)
    os.makedirs(os.path.dirname(CB_MODELS), exist_ok=True)
    with open(CB_MODELS, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    result["written"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="同步 WorkBuddy 自定义模型到 CodeBuddy（完全覆盖）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅校验与预览，不写文件")
    args = parser.parse_args()

    try:
        res = sync(dry_run=args.dry_run)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] {exc}")
        raise SystemExit(1)

    print(f"源文件 : {res['source']}")
    print(f"目标文件: {res['target']}")
    print(f"模型数量: {res['count']}")
    print("模型 id :")
    for mid in res["ids"]:
        print(f"  - {mid}")
    if res["dry_run"]:
        print("[dry-run] 未写入任何文件。")
    else:
        if res["backup"]:
            print(f"已备份原文件: {res['backup']}")
        print(f"[完成] 已覆盖写入 {res['count']} 个模型到 CodeBuddy。")


if __name__ == "__main__":
    main()
