# cherry2workbuddy

将 **Cherry Studio** 中「启用的模型」迁移写入 **WorkBuddy** 的 `models.json`，
实现模型配置（含 Provider 的 base URL 与 API Key）的一键复用。

## 功能特性

- **仅导入启用项**：只迁移模型启用（`is_enabled=1`）且其所属 Provider 也启用的模型。
- **自动补全连接信息**：从 `user_provider` 表解析 base URL（优先取默认聊天端点，回退至任意端点）与首个启用的 API Key。
- **能力自动推断**：
  - 工具调用：`capabilities` 含 `tools` 或 `function-call` 为 `true`；否则默认 `true`。
  - 视觉（图片输入）：`input_modalities` 含 `image` 或 `capabilities` 含 vision/image 标记为 `true`。
  - 音频/视频输入：`input_modalities` 含 `audio` / `video` 时写入 `supportsAudio` / `supportsVideo`。
  - 推理：`reasoning` 字段存在或 `capabilities` 含 `reasoning` 为 `true`。
- **覆盖写入**：以导入集完全替换现有 `models.json`（不保留既不属于 Cherry Studio 的模型），写入前仍自动备份原文件。
- **写入前自动备份**：原文件先备份再覆盖，避免误改。

## Token 限制参数同步

除能力字段外，脚本还会搬运 Cherry Studio 模型配置中的 Token 限制参数：

| Cherry Studio 字段        | WorkBuddy 字段   | 说明                                              |
| ------------------------- | ---------------- | ------------------------------------------------- |
| `max_input_tokens`        | `maxInputTokens` | 最大输入 Token；缺失时回退使用 `context_window`   |
| `max_output_tokens`       | `maxOutputTokens`| 最大输出 Token                                    |
| `context_window`（回退）  | `maxInputTokens` | 仅当 `max_input_tokens` 为空时作为回退值带入      |

参数取值优先级：`user_model` 中已保存的值 > 脚本顶部 `PRESET_TOKEN_DEFAULTS`
的预设默认值（应对 Cherry Studio 仅显示、未落库的 preset 默认，如
`deepseek-v4-flash` 的 1048576 / 393216）> 留空（沿用 WorkBuddy UI 默认值）。
需为其它 preset 补充默认值时，编辑 `PRESET_TOKEN_DEFAULTS` 字典（键为
Cherry Studio 的 `preset_model_id`）。

当前默认仍为「完全覆盖」模式——每次同步会用 Cherry Studio 的导入集
替换整个 `models.json`，WorkBuddy 中独有的模型（如 CF / opencode / modal 等）
将被删除，请知悉此行为。

## 使用方法

```bash
# 可选：覆盖默认路径（Windows 默认已自动定位）
export CHERRY_DB="C:/path/to/cherrystudio.sqlite"
export WB_MODELS="C:/Users/<user>/.workbuddy/models.json"

python cherry_to_workbuddy.py
```

默认路径：

| 配置项       | 默认值                                              |
| ------------ | --------------------------------------------------- |
| `CHERRY_DB`  | `%APPDATA%/CherryStudio/Data/cherrystudio.sqlite`   |
| `WB_MODELS`  | `~/.workbuddy/models.json`                          |

## 双击运行（Windows）

项目根目录提供 `run.bat`，双击即可在命令行窗口中执行迁移（结束后 `pause` 暂停，便于查看结果）：

- **直接双击 `run.bat`**：执行真实迁移并写入 `models.json`。
- **预览不写入**：在文件资源管理器中按住 `Shift` 右键「在此处打开命令窗口」，运行 `run.bat dry`；或在任意终端执行等价命令。

`run.bat` 会自动探测 Python 运行时：优先使用 WorkBuddy 托管的 Python（`%USERPROFILE%\.workbuddy\...`），回退到系统的 `py` / `python`，均未找到时提示错误并退出。窗口开头会显示标题与 Python 路径，结束前暂停，便于查看结果。

**排障**：若双击仍一闪而过或全黑，请在该文件夹按住 `Shift` 右键「在此处打开命令窗口 / 终端」，手动执行 `run.bat`（或 `run.bat dry`）查看具体报错信息。

## 跳过规则

以下模型不会被导入：

- 所属 Provider 未启用；
- 解析不到 base URL；
- 所属 Provider 无可用 API Key（无法鉴权）；
- 非聊天模型，包括 `embedding`、`rerank`、生图（`image_generation` / 输出模态为 `image`）等类型。

## 数据来源

Cherry Studio 模型配置存储于 SQLite 数据库 `cherrystudio.sqlite`：

- `user_model` 表：`is_enabled=1` 表示模型启用；
- `user_provider` 表：通过 `provider_id` 关联以获得 base URL 与 API Key。
