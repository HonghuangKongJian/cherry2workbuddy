#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 Cherry Studio 中「启用的模型」迁移写入 WorkBuddy 的 models.json。

Cherry Studio 模型配置存储于 SQLite 数据库 ``cherrystudio.sqlite`` 的
``user_model`` 表（``is_enabled=1`` 表示启用），通过 ``provider_id`` 关联
``user_provider`` 表以取得 base URL 与 API Key。

WorkBuddy 模型配置为 ``~/.workbuddy/models.json`` 中的模型数组。

本脚本支持：
    * 仅导入「启用」的模型（模型启用且所属 Provider 启用）。
    * 跳过无 base URL、无 API Key 或非聊天模型（embedding / rerank / 生图）的模型。
    * 能力字段（工具调用 / 视觉 / 音频 / 视频 / 推理）依据 Cherry Studio 的
      ``capabilities``、``input_modalities``、``output_modalities`` 推断。
    * 覆盖写入 ``models.json``，写入前自动备份原文件。
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

# Cherry Studio 预设模型的默认 Token 限制。
# 当 user_model 中未显式保存 Token 参数时，按 preset_model_id 回退使用，
# 以同步 Cherry Studio UI 显示的「预设默认值」（这些默认值仅显示、不落库）。
# 注意：预设默认值可能随 Cherry Studio 版本变化，建议优先在 Cherry Studio
# 模型设置中显式填入并保存所需值；此处仅作兜底。
PRESET_TOKEN_DEFAULTS = {
    "deepseek-v4-flash": {"maxInputTokens": 1048576, "maxOutputTokens": 393216},
}


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

    # 读取已启用 Model（含输入/输出模态与端点类型，用于推断能力）
    cur.execute(
        "SELECT provider_id, model_id, preset_model_id, name, is_enabled, "
        "capabilities, input_modalities, output_modalities, endpoint_types, "
        "context_window, max_input_tokens, max_output_tokens, "
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

    Cherry Studio 的能力分散在多个字段中：
        * ``capabilities``: 可能包含 ``reasoning``、``function-call``、
          ``embedding`` 等。
        * ``input_modalities`` / ``output_modalities``: 可能包含
          ``image``、``audio``、``video`` 等。
        * ``reasoning``: JSON 对象，存在即表示支持推理。

    采用规则：
        * 工具调用：``capabilities`` 含 ``tools`` 或 ``function-call`` 为真；
          否则默认 ``True``（OpenAI 兼容聊天端点普遍支持）。
        * 视觉（图片输入）：``input_modalities`` 含 ``image``，或
          ``capabilities`` 含 vision/image 相关标记为真；否则 ``False``。
        * 音频/视频输入：``input_modalities`` 含对应标记即真。
        * 推理：``reasoning`` 字段存在，或 ``capabilities`` 含 ``reasoning``。

    Args:
        model (dict): ``user_model`` 行字典。

    Returns:
        tuple[bool, bool, bool, bool, bool]: (supports_tool_call,
            supports_images, supports_audio, supports_video,
            supports_reasoning)。
    """
    caps = _load_json_field(model.get("capabilities")) or []
    caps_lower = [str(c).lower() for c in caps]
    input_mods = _load_json_field(model.get("input_modalities")) or []
    input_mods_lower = [str(m).lower() for m in input_mods]
    output_mods = _load_json_field(model.get("output_modalities")) or []
    output_mods_lower = [str(m).lower() for m in output_mods]
    has_reasoning_field = model.get("reasoning") is not None

    tool = ("tools" in caps_lower or "function-call" in caps_lower)
    vision = (
        "image" in input_mods_lower
        or any(tok in cap for cap in caps_lower for tok in _VISION_TOKENS)
    )
    audio = "audio" in input_mods_lower or "audio" in output_mods_lower
    video = "video" in input_mods_lower or "video" in output_mods_lower
    reasoning = has_reasoning_field or "reasoning" in caps_lower

    # 没有任何显式能力标记时，按传统聊天模型默认：支持工具，不支持多模态
    if not caps and not input_mods and not output_mods:
        tool = True
        vision = False
        audio = False
        video = False

    return (tool, vision, audio, video, reasoning)


def _is_chat_model(model):
    """判断该模型是否为可导入的聊天模型，排除嵌入/重排/生图等类型。

    Args:
        model (dict): ``user_model`` 行字典。

    Returns:
        bool: True 表示为聊天模型；False 表示应跳过。
    """
    caps = _load_json_field(model.get("capabilities")) or []
    caps_lower = [str(c).lower() for c in caps]
    if any(t in caps_lower for t in ("embedding", "rerank", "image_generation")):
        return False

    endpoint_types = _load_json_field(model.get("endpoint_types")) or []
    et_lower = [str(t).lower() for t in endpoint_types]
    if any(t in et_lower for t in ("image-generation", "rerank")):
        return False

    output_mods = _load_json_field(model.get("output_modalities")) or []
    if "image" in [str(m).lower() for m in output_mods]:
        # 输出模态为图片，视为生图模型而非聊天模型
        return False

    return True


def build_wb_entry(provider, model):
    """将 Cherry Studio 的 (provider, model) 映射为 WorkBuddy 模型条目。

    Args:
        provider (dict): ``user_provider`` 行字典。
        model (dict): ``user_model`` 行字典。

    Returns:
        dict | None: WorkBuddy 模型条目；不可导入（无 base URL / 无 API Key /
            非聊天模型 / 无 model_id）时返回 None。
    """
    base_url = _resolve_base_url(provider)
    if not base_url:
        return None  # 无可用 base URL，无法构造请求地址

    api_key = _resolve_api_key(provider)
    if not api_key:
        return None  # 无可用 API Key，无法鉴权，跳过

    if not _is_chat_model(model):
        return None  # 嵌入 / 重排 / 生图等模型非聊天模型，跳过

    model_id = model.get("model_id")
    if not model_id:
        return None

    tool, vision, audio, video, reasoning = _derive_capabilities(model)

    entry = {
        "id": model_id,
        "name": provider.get('name', 'Custom'),
        "vendor": "Custom",
        "url": base_url,
        "apiKey": api_key,
        "supportsToolCall": tool,
        "supportsImages": vision,
        "supportsReasoning": reasoning,
        "useCustomProtocol": False,
    }
    if audio:
        entry["supportsAudio"] = True
    if video:
        entry["supportsVideo"] = True
    if reasoning:
        entry["reasoning"] = {"canDisableThinking": True}

    # Token 限制参数：优先取 user_model 中已保存的值，缺失时回退到
    # PRESET_TOKEN_DEFAULTS 中对应 preset 的预设默认值。
    preset = PRESET_TOKEN_DEFAULTS.get(model.get("preset_model_id") or "")
    max_out = model.get("max_output_tokens")
    if not max_out and preset:
        max_out = preset.get("maxOutputTokens")
    if max_out:
        entry["maxOutputTokens"] = int(max_out)

    max_in = model.get("max_input_tokens")
    if not max_in:
        max_in = model.get("context_window")
    if not max_in and preset:
        max_in = preset.get("maxInputTokens")
    if max_in:
        entry["maxInputTokens"] = int(max_in)
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
        # 有 Key 的 provider 排在前，其次按名称（忽略大小写）
        return (0 if _resolve_api_key(g["provider"]) else 1,
                g["name"].casefold())

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
    # 按名称升序排序（忽略大小写，避免大写字母排在小写字母之前）
    merged.sort(key=lambda e: e.get("name", "").casefold())

    print(f"[info] 已启用模型(含 provider): {len(raw)}")
    print(f"[info] 可导入(已过滤无URL/无Key/非聊天模型): {len(imported)}")
    print(f"[info] 跳过(无URL/无Key/非聊天模型): {skipped}")
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
