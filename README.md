# Passer 
集成式第二桌面与工具库应用

- 启动运行：python Passer.py
- 内置 `文件夹查看器` `图片查看器` `文本查看器` `PDF 阅览器`
- 内置工具
  - **连点器 Clicker**
  - **随机数 Random**
  - **计划 Plan**
  - **自动化 Automation**
  - **Aira**
  - **计算器 Calculator**
  - **定时关机 Shutdown**
  - **网络检测 Network**
  - **邮件 Mail**
  - **二维码 QR**
  - **Markdown 预览器**
  - **文件搜索 File Search**
  - **屏幕录制 Recorder**
  - **磁力下载 Magnet**
  - **地图 Map**
  - **设备锁 Device Lock**
  - **文件共享 File Share**
  - **设备检测 Device Info**
  - **手机投屏 Phone Mirror**
  - **截图 Screenshot**
  - **注释 Annotate**

- 大模型对话（Aira）需要设置 APIkey
  - 在 `设置` 里打开 **启用大模型对话**，选择默认模型（DeepSeek / Claude / GPT），并填入对应服务商的 API Key（自备）。
  - 启用后主窗口状态栏上方出现 **AI 输入框**，未输入时显示「询问 DeepSeek」之类的占位（随默认模型变化）。
  - 回车提问，`Shift+Enter` 换行；聚焦输入框会自动展开聊天区，点击别处后收回。提问与模型回复以圆角气泡显示（自己靠右、模型靠左）。
  - 可将 Passer 中的单个、多选或组图标拖入 AI 输入框，输入框上方会出现类似附件卡片的显示；发送给模型时会以“该文件：完整路径/地址”的形式附带目标信息。文本类文件会附加一段文本预览，便于模型直接摘要。
  - 调用为各服务商官方接口：DeepSeek（`deepseek-chat`）、Claude（`claude-sonnet-4-6`）、GPT（`gpt-4o-mini`）。仅依赖标准库联网，无需额外安装。API Key 保存在本机 `PasserData/settings.json`。
  - 所有模型调用前都会重新读取 `PasserData/AI_INSTRUCTIONS.md`：DeepSeek/GPT 作为 `system` 消息发送，Claude 通过顶层 `system` 字段发送。可直接编辑此文件来修改 Passer 内 AI 的统一说明。
  - 最近 40 条对话和最多 100 条长期记忆保存在本机 `PasserData/ai_memory.json`，重启后仍会提供给模型。只有用户明确要求记住时，模型才应通过说明文件约定的 `PASSER_MEMORY` 标记写入长期记忆；API Key 和密码不得写入记忆。
  - 模型可按 `AI_INSTRUCTIONS.md` 中的 `PASSER_ACTION` 协议操作 Passer：列出或搜索项目、选择和定位图标、打开现有项目或内置工具、将用户明确提供的路径/网址载入 Passer、启动截图工具、添加/列出本次运行期内的闹钟、标注图标颜色、清除搜索。协议采用严格 JSON，一次最多执行 5 项；删除、移除、重命名、移动、覆盖文件和直接锁定设备均不开放。

- 运行时 MOD Loader
  - `Passer.exe` 是宿主，MOD 是宿主外的运行时代码，保存在当前数据目录的 `PasserData\Mods\<id>`。因此发布 EXE 后可以继续新增、编辑、禁用或删除 MOD，无需修改 EXE，也无需重新打包，思路与 Minecraft 的 Mod Loader 相同。
  - 启用且 `startup: true` 的 MOD 会在 Passer 启动时导入，并调用 `setup_mod(context)`。它可以给主界面添加标题栏按钮、注册多个可搜索/可固定的工具、注册 Aira 动作、监听宿主事件和安排 UI 定时回调。
  - `open_mod(context)` 是可选入口。定义它并设置 `expose_tool: true` 时，该 MOD 自身会作为一个工具出现在搜索结果中；只有 `setup_mod` 的“纯扩展 MOD”不会生成无意义的主入口图标。
  - Aira 在「无瑕授权」下可使用 `create_mod`、`edit_mod`、`enable_mod`、`disable_mod`、`delete_mod`、`reload_mods`；`list_mods` 为只读动作。保存后的 MOD 会立即热重载，旧按钮、工具、事件、定时器和 AI 动作会先安全卸载，再载入新版本。
  - 稳定 API 由 `passer_module_api.ModContext` 提供。基础能力包括主题/字体/颜色、状态栏、向 Passer 载入路径和 `load_state` / `save_state`；扩展能力包括 `register_tool`、`register_toolbar_button`、`register_ai_action`、`on`、`call_later` 和 `register_cleanup`。
  - 当前事件包括 `app_ready`、`items_changed`、`before_item_open`、`after_item_open`、`search_changed`、`theme_changed`、`shutdown`。事件回调接收 `(payload, context)`。
  - 可以实现 `teardown_mod(context)`、让 `setup_mod` 返回清理函数，或调用 `context.register_cleanup(...)`。禁用、热重载和退出 Passer 时都会执行清理，并撤销该 MOD 注册到宿主的内容。
  - API 版本写在 manifest 的 `api` 字段中。版本不匹配或加载失败的 MOD 会被隔离，错误会显示在「设置 → 工具与 MOD」和 `list_mods` 中，不阻断其他 MOD 或 Passer 启动。
  - MOD 在 Passer 主进程内运行，拥有与 Passer 相同的本机权限，只安装可信代码。外置 MOD 可直接使用 Python 标准库；额外第三方库仍需随 MOD 提供，或已被宿主 EXE 收录。

- 装载 MOD
最小目录结构：
```text
PasserData/Mods/my_mod/
├─ manifest.json
├─ mod.py
└─ data/
```

典型 `manifest.json`：

```json
{
  "id": "my_mod",
  "title": "My MOD",
  "version": "1.0.0",
  "api": 1,
  "entry": "mod.py",
  "callable": "open_mod",
  "setup": "setup_mod",
  "startup": true,
  "expose_tool": false,
  "enabled": true
}
```

运行时扩展示例：

```python
import tkinter as tk

class Controller:
    def __init__(self, window):
        self.window = window
        self.closed = False

    def show(self):
        self.window.deiconify()
        self.window.lift()

    def close(self):
        self.closed = True
        self.window.destroy()


def open_panel(context):
    window = tk.Toplevel(context.root)
    window.title("My MOD")
    tk.Label(window, text="Hello MOD").pack(padx=24, pady=24)
    return Controller(window)


def on_search(payload, context):
    context.save_state({"last_query": payload.get("query", "")})


def aira_echo(params, context):
    return {"mod": context.mod_id, "echo": params.get("text", "")}


def setup_mod(context):
    context.register_tool("panel", "My MOD Panel", open_panel, aliases=["panel"])
    context.register_toolbar_button("hello", "MOD", open_panel)
    context.register_ai_action("echo", aira_echo, description="回显一段文字")
    context.on("search_changed", on_search)
    context.write_status("My MOD 已载入")


def teardown_mod(context):
    context.write_status("My MOD 已卸载")
```

上面的 AI 动作会自动命名为 `mod.my_mod.echo`，避免不同 MOD 之间重名。
