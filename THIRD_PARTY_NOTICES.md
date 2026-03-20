# Third-Party Notices

本文件用于记录本项目使用的主要第三方开源组件及其许可证信息。
许可证条款以各上游项目仓库和发布包中的原始 LICENSE/NOTICE 为准。

## 1. 核心依赖

| 组件 | 用途 | 上游地址 | 许可证 |
|---|---|---|---|
| PySide6 | 桌面 UI 与多媒体 | https://doc.qt.io/qtforpython-6/ | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| yt-dlp | 视频元数据解析与下载 | https://github.com/yt-dlp/yt-dlp | Unlicense / Public Domain |
| imageio-ffmpeg | FFmpeg 可执行文件发现与调用 | https://github.com/imageio/imageio-ffmpeg | BSD-2-Clause |
| FFmpeg | 媒体处理与抽帧 | https://ffmpeg.org/ | LGPL/GPL（取决于构建方式） |
| requests | HTTP 请求 | https://github.com/psf/requests | Apache-2.0 |
| psutil | 进程管理 | https://github.com/giampaolo/psutil | BSD-3-Clause |
| curl-cffi | 网络请求伪装能力 | https://github.com/lexiforest/curl_cffi | MIT |
| unalix | URL 清洗 | https://github.com/AmanoTeam/Unalix | LGPL-3.0 |

## 2. 插件与外部服务说明

- 本仓库包含 Chrome 扩展目录 `chrome_extension/`，用于与桌面程序本地桥接联动执行“以图搜图”工作流。
- “以图搜图”流程会打开浏览器页面并在用户本地会话中执行，相关页面与服务条款由对应网站定义。

## 3. 归属与合规声明

- 本项目与上述第三方项目及相关网站不存在官方从属关系。
- 使用者应自行遵守所在地区法律法规、目标平台条款及内容版权规则。
- 若你进行二次分发，请同时附带本文件与各依赖要求的许可证文本。
