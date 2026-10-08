# Passer 

 Windows 中转软件。

## 下载与运行

[下载 Passer v1.6.1 Windows x64 固定运行库版](https://github.com/linsincos/Passer/releases/download/v1.6.1/Passer-v1.6.1-windows-x64-onedir.zip)。

解压完整 ZIP 后，运行 `Passer/Passer.exe`。`PasserRuntime` 必须与 `Passer.exe` 放在同一目录，请移动整个 `Passer` 文件夹，或为其中的 EXE 创建桌面快捷方式。

```text
Passer/
├─ Passer.exe
└─ PasserRuntime/
```

此版本直接使用旁边的运行库，不需要每次启动解压 `_MEI` 临时目录。无需安装 Python；个人数据仍使用已有的 PasserData 位置，新安装时按提示选择数据目录。

## 从源码启动

运行：

```powershell
python Passer.py
```

## 用法

- 拖入文件
- 拖入本地文件时，Passer 只保存源路径作为快捷入口，不复制原文件；双击图标会直接打开源文件。
- 复制文件/文件夹后，点 `粘贴`，或在窗口里按 `Ctrl+V`。
- 复制网址、本地路径、`file:///C:/...` 链接后，点 `粘贴`，或按 `Ctrl+V`。
- 复制截图、网页图片等图片内容后，按 `Ctrl+V`，会自动保存为 PNG 并生成图标。
- 复制普通文本后，按 `Ctrl+V`，会自动保存为 TXT 并生成图标；文件名自动取文本开头的一段内容。
- 勾选 `置顶` 可让窗口保持置顶；勾选右侧的 `自启动` 可设置开机自动启动（写入当前用户的启动项，取消勾选即移除）。
- 文件名过长时，图标下方名称最多显示两行，超出部分以 `…` 省略，避免与图标重叠。
- 图标沿用 Windows 文件夹图标、文件类型图标或默认应用图标；图片文件以其缩略图显示。
- 单击图标选中，双击图标打开目标。
- 按住 `Ctrl` 单击可多选/取消单个图标；按住 `Shift` 单击可连续选择；在空白处拖拽可框选多个图标。
- 将本地文件/文件夹图标拖出主窗口到资源管理器、聊天窗口、编辑器等其他应用时，会以复制方式把对应文件/文件夹拖放到目标位置；在主窗口内拖动仍用于调整图标位置。
- 选中图标后按 `Delete`，只会从 Passer 中移除图标。
- 点 `删除原文件` 或右键选择 `删除原文件`，确认后会把本地原文件/文件夹移到回收站，并移除对应图标。
- 右键图标可打开、在资源管理器中显示、复制、复制文件、复制路径、重命名、删除原文件或移除；其中 `复制` 遇到文本类文件会直接复制文件内容，`复制文件` 才会把文件本身放到剪贴板供资源管理器粘贴。把文本文件图标拖到窗口外部时仍然复制文本文件本身。
- 顶部搜索支持名称/路径、拼音/首字母、模糊匹配，并会优先显示最近使用过的结果。
- 右键 PDF 图标会多出 `用 Zotero 打开`（需已安装 Zotero）。
- 右键空白处可 `粘贴` 或 `刷新`（重新加载所有图标）。

## 文件夹查看器

- 双击文件夹图标会用内置文件夹查看器打开，采用与主窗口一致的自定义窗口（可拖动标题栏、右下角缩放、最小化、关闭）。
- 文件夹内容以类似主界面的图标网格显示；支持 `后退`、`上一级`、`刷新`、`资源管理器`。
- 双击子文件夹会在当前查看器内进入；双击图片、PDF、文本会继续使用对应内置查看器，其他文件使用系统默认程序打开。
- 选中文件或子文件夹后可 `添加到 Passer`，也可右键打开、添加、在资源管理器中显示或复制路径。

## 图片查看器

- 双击图片图标会用内置查看器打开，采用与主窗口一致的自定义窗口（可拖动标题栏、右下角缩放、最小化、关闭）。
- 默认以 **50%** 显示，窗口长宽自动适应到图片的 50%。
- 左上角按钮：`适应`、`原始`、`编辑`。`编辑` 进入批注模式，出现画笔工具条（画笔 / 矩形 / 箭头 / 文字、颜色、撤销、清除、复制、保存、完成）。`保存` 会把批注合并并覆盖原图片，`复制` 把合并后的图片复制到剪贴板。

## 文本查看器

- 双击文本类文件（`.txt`、`.md`、`.log`、`.csv`、`.json`、`.ini`、常见代码/配置等）会用内置文本查看器打开（自定义窗口）。
- 可直接编辑文字；按钮：`A-`/`A+` 调整字号、`自动换行` 切换、`保存`（写回原文件，或按 `Ctrl+S`）、`默认程序打开`。
- 保存会沿用原文件编码（UTF-8 / GBK / UTF-16 等）。超大文件只载入前 4 MB，并禁止保存以免截断。
- 有未保存的修改时关闭会提示“保存 / 不保存 / 取消”。

## PDF 阅览器

- 双击 `.pdf` 图标会用内置 PDF 阅览器打开（与主窗口一致的自定义窗口）。
- **连续阅读模式**：所有页面竖向连排，滚轮/滚动条/`↑↓`、`PgUp/PgDn`、`空格`、`Home/End` 连续滚动，不直接换页。`Ctrl+滚轮` 缩放。
- 左上角按钮：`适应`（按宽度铺满）、`原始`、`放大`/`缩小`、`编辑`、`默认程序打开`（用系统 PDF 程序打开）。
- `编辑` 进入批注模式（画笔 / 矩形 / 箭头 / 文字、颜色、撤销、清除），可在任意页上批注（**逐页**保存）。
- `复制本页` 把当前页（含批注）复制到剪贴板；`保存批注PDF` 把各页（含批注）导出为同目录下的新 PDF（按页转图片），并在主窗口生成图标。
- 有未保存的批注时关闭会提示“保存 / 不保存 / 取消”。
- 需要 `pypdfium2`（本机已安装）。缺少时 `.pdf` 会改用系统默认程序打开：`python -m pip install pypdfium2`。

## 文件历史版本

- 文件图标右键的“默认应用打开”下方提供“历史版本”子菜单；悬停可查看并打开任一历史快照。
- 图片、PDF、文本、Excel、Office 快速预览、音视频和压缩包等内置查看器均可点“另存为新版本”。快照保存在 `PasserData/Versions`，不会覆盖原文件；有版本的图标右上角显示 `+n`。
- `设置 → 通用 → 历史版本命名` 可选择时间、版本号或自定义模板。模板支持 `{name}`、`{stem}`、`{ext}`、`{date}`、`{time}`、`{datetime}`、`{version}`，也可把需求填入 Aira 对话并在确认后自动保存规则。

## 内置工具（搜索框输入名称即可打开）

- 在搜索结果里，**内置工具 / 系统工具** 行的右侧会出现一个 📌 图标，点它即可把该工具作为图标 **添加到 Passer**（按 target 去重，重复添加无效）。
- 添加后的工具图标：双击打开该工具，**右键 → 移除** 可从 Passer 中删除（不影响其它图标）。搜索栏本身仍不显示图标，和原来一样。


- **连点器 Clicker**：在固定坐标 / 当前鼠标位置自动点击。
  - 算法：`时间间隔`（毫秒）、`每秒次数`、`自由连点`（点当前光标处）。
  - 勾选 `多点顺序点击` 后可记录多个目标点，按列表顺序循环点击，支持 `添加点 / 删除 / 上移 / 下移 / 清空`。
  - `滞后` 为开始前的等待秒数，`时长` 为运行总秒数（填 0 表示一直点到手动停止）。
  - `终止键` 是一个**全局热键**（默认 `F8`），即使 Passer 在后台、连点正在进行也能按下立即停止。
  - 窗口大小固定，切换单点 / 多点形态不会改变窗口尺寸。
- **随机数 Random**：设置 `最小值 / 最大值 / 数量` 后点 `生成`（按 `Enter` 也可生成）；可选 `不重复`、`从小到大排序`；点 `复制` 把结果复制到剪贴板（结果框也可手动选中后 `Ctrl+C`）。
- **闹钟 Alarm**：填 `时间`（如 `14:30`，只填时间则取今天、已过则顺延明天；也支持 `06-20 09:00`、`2026-06-20 09:00`）和 `事件`，点 `添加提醒`。到点会弹出 **Windows 通知**（即使闹钟窗口已关闭，只要 Passer 在运行）。列表展示所有待提醒、底部显示「即将提醒」的下一条，可 `删除 / 清空`。注：待提醒列表在程序运行期间有效，重启不保留。
- **计算器 Calculator**：支持表达式计算、长度/重量/容量/面积/温度等单位换算，以及在线汇率换算。
- **定时关机 Shutdown**：可按指定时间关机，也可设置倒计时关机；计划设置前会确认，并可用 `取消关机` 撤销 Windows 关机计划。
- **网络检测 Network**：只有一个 `开始检测` 按钮，完成后自动显示下载速度、延迟和丢包率。
- **服务器 Server**：选择本地目录后启动只读静态 HTTP 服务，可自行设置监听地址和端口；默认 `127.0.0.1` 仅本机访问，填写 `0.0.0.0` 可供局域网访问。可选 SSH 网关会使用 Windows OpenSSH 建立反向隧道，支持配置网关主机、账户、SSH 端口和远端地址/端口，并可选择私钥/SSH Agent 或账户密码认证。密码只在每次连接时弹窗输入，不保存到配置文件、命令行或日志，也不开放远程命令。
- **剪贴板 Clipboard**：窗口打开期间查看当前剪贴板和最多 40 条内存历史，支持编辑文本后写回、图片预览/另存、暂停记录以及分别清空系统剪贴板或历史；关闭窗口后历史即释放，不写入磁盘。
- **文件共享 File Share**：显示局域网 IP 和广域网 IP；优先通过广播发现设备，也可按需直连探测同网段。传输支持断点续传、SHA-256 完整性校验、取消和重试；传输码支持 4–8 位数字（推荐 6 位以上），新版之间使用 PBKDF2/HMAC 挑战鉴权，不直接在网络中发送传输码，并对连续失败进行限流。
- **二维码 QR**：输入内容生成二维码 PNG 并载入 Passer；识别图片会尝试调用本机 pyzbar/OpenCV，缺少识别库时会提示。
- **屏幕录制 Recorder**：框选区域录制；GIF 使用内置 Pillow 保存，MP4 需要本机具备 imageio/ffmpeg 编码支持。
- **设备锁 Device Lock**：选择禁用键盘和/或鼠标，设置 2–8 位英文数字密码并确认（不区分大小写）。锁定后设置窗口自动关闭且不显示顶部提示；即使键盘已被禁用，直接键入密码仍会自动解除锁定，无需按回车。上次密码会使用当前 Windows 用户绑定的 DPAPI 加密保存，上次选择禁用的设备也会记录，并在下次打开时自动恢复。
- **设备检测 Device Info**：一键查看当前设备的 CPU（型号 / 核心 / 线程 / 主频）、主板（厂商 / 型号）、内存（每条型号、容量、类型 DDR3/4/5、频率，以及总容量）、硬盘（型号、容量、接口）和 GPU（型号、显存）。打开窗口自动检测一次，可点 `刷新` 重新检测，也可点 `复制` 把结果复制到剪贴板。底层走 PowerShell + WMI；大显存 GPU 通过注册表 `qwMemorySize` 校正，避免被 `Win32_VideoController.AdapterRAM` 的 4GB UINT32 截断。

## 大模型对话（AI）

- 在 `设置` 里打开 **启用大模型对话**，选择默认模型（DeepSeek / Claude / GPT），并填入对应服务商的 API Key（自备）。
- **启用外置接口** 位于 **启用 Aira** 下方：Aira 开启时默认开启，也可独立关闭；关闭 Aira 时该接口会被强制关闭。只有接口开启后，OpenClaw 等外部代理才能调用 Passer，手机端才能新增需要调用模型的 Aira 任务；Passer 内部的 Aira 对话不受该独立开关影响。
- 启用后主窗口状态栏上方出现 **AI 输入框**，未输入时显示「询问 DeepSeek」之类的占位（随默认模型变化）。
- 回车提问，`Shift+Enter` 换行；聚焦输入框会自动展开聊天区，点击别处后收回。提问与模型回复以圆角气泡显示（自己靠右、模型靠左）。
- 可将 Passer 中的单个、多选或组图标拖入 AI 输入框，输入框上方会出现类似附件卡片的显示；发送给模型时会以“该文件：完整路径/地址”的形式附带目标信息。文本类文件会附加一段文本预览，便于模型直接摘要。
- 调用为各服务商官方接口：DeepSeek（`deepseek-chat`）、Claude（`claude-sonnet-4-6`）、GPT（`gpt-4o-mini`）。仅依赖标准库联网，无需额外安装。API Key 保存在本机 `PasserData/settings.json`。
- 所有模型调用前都会重新读取 `PasserData/AI_INSTRUCTIONS.md`：DeepSeek/GPT 作为 `system` 消息发送，Claude 通过顶层 `system` 字段发送。可直接编辑此文件来修改 Passer 内 AI 的统一说明。
- 最近 40 条对话和最多 100 条长期记忆保存在本机 `PasserData/ai_memory.json`，重启后仍会提供给模型。只有用户明确要求记住时，模型才应通过说明文件约定的 `PASSER_MEMORY` 标记写入长期记忆；API Key 和密码不得写入记忆。
- 模型可按 `AI_INSTRUCTIONS.md` 中的 `PASSER_ACTION` 协议操作 Passer：列出或搜索项目、选择和定位图标、打开现有项目或内置工具、将用户明确提供的路径/网址载入 Passer、启动截图工具、添加/列出本次运行期内的闹钟、标注图标颜色、清除搜索。协议采用严格 JSON，一次最多执行 5 项；删除、移除、重命名、移动、覆盖文件和直接锁定设备均不开放。

## 运行时 MOD Loader（EXE 发布后仍可扩展）

- `Passer.exe` 是宿主，MOD 是宿主外的运行时代码，保存在当前数据目录的 `PasserData\Mods\<id>`。因此发布 EXE 后可以继续新增、编辑、禁用或删除 MOD，无需修改 EXE，也无需重新打包，思路与 Minecraft 的 Mod Loader 相同。
- 启用且 `startup: true` 的 MOD 会在 Passer 启动时导入，并调用 `setup_mod(context)`。它可以给主界面添加标题栏按钮、注册多个可搜索/可固定的工具、注册 Aira 动作、监听宿主事件和安排 UI 定时回调。
- `open_mod(context)` 是可选入口。定义它并设置 `expose_tool: true` 时，该 MOD 自身会作为一个工具出现在搜索结果中；只有 `setup_mod` 的“纯扩展 MOD”不会生成无意义的主入口图标。
- Aira 在「无瑕授权」下可使用 `create_mod`、`edit_mod`、`enable_mod`、`disable_mod`、`delete_mod`、`reload_mods`；`list_mods` 为只读动作。保存后的 MOD 会立即热重载，旧按钮、工具、事件、定时器和 AI 动作会先安全卸载，再载入新版本。
- 稳定 API 由 `passer_module_api.ModContext` 提供。基础能力包括主题/字体/颜色、状态栏、向 Passer 载入路径和 `load_state` / `save_state`；扩展能力包括 `register_tool`、`register_toolbar_button`、`register_ai_action`、`on`、`call_later` 和 `register_cleanup`。
- 当前事件包括 `app_ready`、`items_changed`、`before_item_open`、`after_item_open`、`search_changed`、`theme_changed`、`shutdown`。事件回调接收 `(payload, context)`。
- 可以实现 `teardown_mod(context)`、让 `setup_mod` 返回清理函数，或调用 `context.register_cleanup(...)`。禁用、热重载和退出 Passer 时都会执行清理，并撤销该 MOD 注册到宿主的内容。
- API 版本写在 manifest 的 `api` 字段中。版本不匹配或加载失败的 MOD 会被隔离，错误会显示在「设置 → 工具与 MOD」和 `list_mods` 中，不阻断其他 MOD 或 Passer 启动。
- MOD 在 Passer 主进程内运行，拥有与 Passer 相同的本机权限，只安装可信代码。外置 MOD 可直接使用 Python 标准库；额外第三方库仍需随 MOD 提供，或已被宿主 EXE 收录。

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
