# cherry2workbuddy

将 **Cherry Studio** 中「启用的模型」迁移写入 **WorkBuddy** 的 `models.json`，
实现模型配置（含 Provider 的 base URL 与 API Key）的一键复用。

## 功能特性

- **仅导入启用项**：只迁移模型启用（`is_enabled=1`）且其所属 Provider 也启用的模型。
- **自动补全连接信息**：从 `user_provider` 表解析 base URL（优先取默认聊天端点，回退至任意端点）与首个启用的 API Key。
- **能力自动推断**：
  - 显式声明 `capabilities` 时按标记解析（工具调用 / 视觉 / 推理）。
  - 未声明时默认支持工具调用，视觉按 `False`，推理按 `reasoning` 字段是否存在。
- **安全合并**：与现有 `models.json` 按 `id` 去重合并，Cherry Studio 数据优先。
- **写入前自动备份**：原文件先备份再覆盖，避免误改。

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

`run.bat` 会自动探测 Python 运行时：优先使用 WorkBuddy 托管的 Python，回退到系统的 `py` / `python`，均未找到时提示错误并退出。

## 跳过规则

以下模型不会被导入：

- 所属 Provider 未启用；
- 解析不到 base URL；
- 仅含 `embedding` 能力（非聊天模型）。

## 数据来源

Cherry Studio 模型配置存储于 SQLite 数据库 `cherrystudio.sqlite`：

- `user_model` 表：`is_enabled=1` 表示模型启用；
- `user_provider` 表：通过 `provider_id` 关联以获得 base URL 与 API Key。
