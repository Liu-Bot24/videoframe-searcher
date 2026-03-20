# VideoFrame Searcher

基于 PRD 实现的 Windows 桌面工具，支持：

- `yt-dlp` 完整视频下载（音视频合并、最佳可用画质、可配自定义格式/高级参数、可选 Cookie 授权）
- X/Twitter 下载失败时自动切换备用解析与下载通道（基于推文公开视频直链）
- 本地视频上传（与下载流程一致，可直接进入后续抽帧流程）
- `FFmpeg` 抽帧（首帧强制保留 + 间隔抽帧，支持重抽帧清理与容量预警）
- 项目工作区管理（`YYYYMMDD_HHMMSS_标题`，标题自动截断与非法字符过滤）
- 历史项目回溯、删除（按钮与右键）与分页懒加载画廊（防止一次性加载导致内存问题）
- 画廊页支持一键打开当前项目截图文件夹
- 画廊页支持多图“搜索已选截图”（Chrome 插件协同，任务队列自动执行）
- 设置页中的代理配置与 yt-dlp 强制更新

## 快速启动

```powershell
python run.py
```

`run.py` 会自动安装缺失依赖。

或双击：

```text
start.bat
```

### Chrome 插件协同搜索

1. 在 Chrome 打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择目录：

```text
chrome_extension
```

2. 启动主程序（`python run.py` 或 `start.bat`）。  
主程序会自动拉起本地桥接服务（端口 `38999`），不需要手动运行 bat。

3. 点击扩展图标 `VFS Google Lens Uploader`，将插件状态切换到“开启”。
4. 在主程序画廊页选中 1 张截图，点击“搜索已选截图”。

说明：
- 桥接默认图片路径仍为：`workspace\20260320_123347_#渣男探花_#探花系列_三好学生，团支书\screenshots\frame_00010.jpg`
- 桥接日志：`logs/chrome_extension_bridge.log`
- 插件仅有“开启/关闭”两种状态：关闭时主程序会提示需要开启插件。
- 该方案在真实 Chrome 会话内上传，通常比跨会话 URL 复用更稳定；Google 风控出现时仍可能需要人工验证。
- `start_chrome_extension_bridge.bat` 仅保留为排障用途（正常使用无需手动运行）。

### 下载能力说明（完整版）

- 默认下载格式：`bv*+ba/b`（最佳音视频组合，自动回退）
- 可在“设置”中自定义：
  - 下载格式（如 `best`, `bv*[height<=2160]+ba/b`）
  - 合并封装格式（`mp4/mkv/webm/...`）
  - yt-dlp 高级参数（原样透传）
- 下载链路不再限制“1080p以内仅视频流”。

## 日志

- 应用日志：`logs/app.log`
- 启动器日志：`logs/launcher.log`

启动失败或运行异常时，可直接查看上述日志定位问题。

## 目录说明

- `run.py`: 自动依赖安装 + 应用启动入口
- `videoframe_searcher/ui/main_window.py`: 主界面与交互逻辑
- `videoframe_searcher/services/*`: 下载、抽帧、项目管理、进程管理等服务
- `config/settings.json`: 本地设置（首次运行自动创建）
