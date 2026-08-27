#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 Cherry Studio 中「启用的模型」迁移写入 WorkBuddy 的 models.json。

Cherry Studio 模型配置存储于 SQLite 数据库 ``cherrystudio.sqlite`` 的
``user_model`` 表（``is_enabled=1`` 表示启用），通过 ``provider_id`` 关联
``user_provider`` 表以取得 base URL 与 API Key。

WorkBuddy 模型配置为 ``~/.workbuddy/models.json`` 中的模型数组。

本脚本支持：
    * 仅导入「启用」的模型（模型启用且所属 Provider 启用）。
    * 跳过无 base URL 或仅含 ``embedding`` 能力的模型。
    * 能力字段（工具调用 / 视觉 / 推理）依据 Cherry Studio 数据推断。
    * 与现有 ``models.json`` 合并，按 ``id`` 去重，Cherry Studio 数据优先。
    * 写入前自动备份原文件。
"""

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# --------------------------------------------------------------------------- #
# 路径配置（Windows 默认值，可用环境变量覆盖）
# --------------------------------------------------------------------------- #
DEFAULT_CHERRY_DB = os.path.join(
    os.environ.get("APPDATA", ""),
    "CherryStudio", "Data", "cherrystudio.sqlite",
)
DEFAULT_WB_MODELS = os.path.join(
    os.path.expanduser("~"), ".workbuddy", "models.json",
)

CHERRY_DB = os.environ.get("CHERRY_DB", DEFAULT_CHERRY_DB)
WB_MODELS = os.environ.get("WB_MODELS", DEFAULT_WB_MODELS)

# 视觉相关能力标记
_VISION_TOKENS = ("vision", "image", "image-input", "multimodal", "image_generation")


def _load_json_field(raw):
    """解析可能为 JSON 字符串 / None 的字段。

    Args:
        raw (str | None): 数据库中的原始字段值。

    Returns:
        list | dict | None: 解析后的对象，解析失败或为空时返回对应空值。
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def read_cherry_models(db_path):
    """读取 Cherry Studio 中所有「启用」的模型及其所属 Provider 信息。

    Args:
        db_path (str): ``cherrystudio.sqlite`` 文件路径。

    Returns:
        list[dict]: 每项包含 provider 与 model 的扁平化字段。
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 读取已启用 Provider
    cur.execute(
        "SELECT provider_id, name, is_enabled, endpoint_configs, api_keys, "
        "default_chat_endpoint FROM user_provider"
    )
    providers = {}
    for p in cur.fetchall():
        providers[p["provider_id"]] = dict(p)

    # 读取已启用 Model
    cur.execute(
        "SELECT provider_id, model_id, name, is_enabled, capabilities, "
        "reasoning FROM user_model WHERE is_enabled = 1"
    )
    models = cur.fetchall()
    con.close()

    result = []
    for m in models:
        m = dict(m)
        provider = providers.get(m["provider_id"])
        if not provider or provider["is_enabled"] != 1:
            continue  # 跳过已禁用的 Provider
        result.append({"provider": provider, "model": m})
    return result


def _resolve_base_url(provider):
    """从 Provider 的 endpoint_configs 中解析 base URL。

    Args:
        provider (dict): ``user_provider`` 行字典。

    Returns:
        str | None: 解析到的 base URL，未找到返回 None。
    """
    ec = _load_json_field(provider["endpoint_configs"]) or {}
    default_ep = provider.get("default_chat_endpoint")
    # 优先使用默认聊天端点
    if default_ep and default_ep in ec and ec[default_ep].get("baseUrl"):
        return ec[default_ep]["baseUrl"].rstrip("/")
    # 回退到任意含 baseUrl 的端点
    for cfg in ec.values():
        if isinstance(cfg, dict) and cfg.get("baseUrl"):
            return cfg["baseUrl"].rstrip("/")
    return None


def _resolve_api_key(provider):
    """从 Provider 的 api_keys 中取得首个启用的 Key。

    Args:
        provider (dict): ``user_provider`` 行字典。

    Returns:
        str: API Key，未找到返回空字符串。
    """
    keys = _load_json_field(provider["api_keys"]) or []
    for k in keys:
        if isinstance(k, dict) and k.get("isEnabled", True):
            return k.get("key", "")
    return ""


def _derive_capabilities(model):
    """推断 WorkBuddy 所需的能力布尔字段。

    Cherry Studio 的 ``capabilities`` 字段大多为空，因此采用如下规则：
        * 若显式声明能力：直接按标记解析。
        * 若未声明：默认支持工具调用（OpenAI 兼容聊天端点普遍支持），
          视觉按 False，推理按 ``reasoning`` 字段是否存在。

    Args:
        model (dict): ``user_model`` 行字典。

    Returns:
        tuple[bool, bool, bool]: (supports_tool_call, supports_images,
            supports_reasoning)。
    """
    caps = _load_json_field(model.get("capabilities")) or []
    has_reasoning_field = model.get("reasoning") is not None
    if not caps:
        return (True, False, has_reasoning_field)
    caps_lower = [str(c).lower() for c in caps]
    tool = "tools" in caps_lower
    vision = any(tok in cap for cap in caps_lower for tok in _VISION_TOKENS)
    reasoning = has_reasoning_field or "reasoning" in caps_lower
    return (tool, vision, reasoning)


def build_wb_entry(provider, model):
    """将 Cherry Studio 的 (provider, model) 映射为 WorkBuddy 模型条目。

    Args:
        provider (dict): ``user_provider`` 行字典。
        model (dict): ``user_model`` 行字典。

    Returns:
        dict | None: WorkBuddy 模型条目；不可导入（无 base URL / 无 API Key /
            embedding 模型 / 无 model_id）时返回 None。
    """
    base_url = _resolve_base_url(provider)
    if not base_url:
        return None  # 无可用 base URL，无法构造请求地址

    api_key = _resolve_api_key(provider)
    if not api_key:
        return None  # 无可用 API Key，无法鉴权，跳过

    caps = _load_json_field(model.get("capabilities")) or []
    if "embedding" in [str(c).lower() for c in caps]:
        return None  # embedding 模型非聊天模型，跳过

    model_id = model.get("model_id")
    if not model_id:
        return None

    display = model.get("name") or model_id
    tool, vision, reasoning = _derive_capabilities(model)

    entry = {
        "id": model_id,
        "name": f"{provider.get('name', 'Custom')} / {display}",
        "vendor": "Custom",
        "url": base_url,
        "apiKey": api_key,
        "supportsToolCall": tool,
        "supportsImages": vision,
        "supportsReasoning": reasoning,
        "useCustomProtocol": False,
    }
    if reasoning:
        entry["reasoning"] = {"canDisableThinking": True}
    return entry


def print_provider_summary(raw):
    """在控制台打印 Provider 维度摘要：已启用 Provider 数量、有/无 API Key
    的数量，以及各 Provider 提供的模型清单。

    Args:
        raw (list[dict]): ``read_cherry_models`` 返回结果，每项含 provider
            与 model 字段。
    """
    groups = {}  # provider_id -> {"name", "provider", "models": []}
    for item in raw:
        p = item["provider"]
        pid = p["provider_id"]
        g = groups.setdefault(
            pid,
            {"name": p.get("name", pid), "provider": p, "models": []},
        )
        m = item["model"]
        g["models"].append(m.get("name") or m.get("model_id"))

    def _order(g):
        # 有 Key 的 provider 排在前，其次按名称
        return (0 if _resolve_api_key(g["provider"]) else 1, g["name"])

    ordered = sorted(groups.values(), key=_order)
    with_key = [g for g in ordered if _resolve_api_key(g["provider"])]
    no_key = [g for g in ordered if not _resolve_api_key(g["provider"])]

    print(
        f"[summary] 已启用 Provider: {len(ordered)} 个 | "
        f"有 Key: {len(with_key)} | 无 Key: {len(no_key)}"
    )
    print("[summary] Provider 明细 (有 Key 在前):")
    for g in ordered:
        has = bool(_resolve_api_key(g["provider"]))
        tag = "[OK]" if has else "[--]"
        print(f"  {tag} {g['name']}  ({len(g['models'])} 个模型)")
        for mn in g["models"]:
            print(f"        - {mn}")


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"[info] Cherry DB : {CHERRY_DB}")
    print(f"[info] WB models : {WB_MODELS}")

    if not os.path.exists(CHERRY_DB):
        print(f"[error] 未找到 Cherry Studio 数据库: {CHERRY_DB}")
        sys.exit(1)

    raw = read_cherry_models(CHERRY_DB)
    print_provider_summary(raw)
    imported = []
    skipped = 0
    for item in raw:
        entry = build_wb_entry(item["provider"], item["model"])
        if entry is None:
            skipped += 1
            continue
        imported.append(entry)

    # 处理导入集内部的 id 冲突：追加 provider 短标识
    seen = {}
    for entry in imported:
        iid = entry["id"]
        if iid in seen:
            entry["id"] = f"{iid}__{entry['url'].split('//')[-1].split('/')[0]}"
        seen.setdefault(iid, 0)
        seen[iid] += 1

    # 覆盖模式：以 Cherry Studio 导入集完全替换现有 models.json，
    # 不保留 WorkBuddy 中既不属于 Cherry Studio 的模型；写入前仍自动备份。
    merged = imported
    merged.sort(key=lambda e: e.get("name", ""))

    print(f"[info] 已启用模型(含 provider): {len(raw)}")
    print(f"[info] 可导入(已过滤无URL/无Key/embedding): {len(imported)}")
    print(f"[info] 跳过(无URL/无Key/embedding): {skipped}")
    print(f"[info] 模式: 覆盖写入，将写入 {len(merged)} 条模型")

    if dry_run:
        print("[info] DRY-RUN 模式，未写入文件。预览前 5 条:")
        for e in merged[:5]:
            print("   -", e["id"], "|", e["name"], "|", e["url"])
        return

    # 写入前备份
    if os.path.exists(WB_MODELS):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{WB_MODELS}.bak_{ts}"
        shutil.copy2(WB_MODELS, backup)
        print(f"[info] 已备份原文件: {backup}")

    with open(WB_MODELS, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[ok] 已写入 {len(merged)} 条模型到 {WB_MODELS}")


if __name__ == "__main__":
    main()
