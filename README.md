<div align="center">

# Image Search Gallery

Languages: [简体中文](README.md) · [English](README-en.md)

</div>

Image Search Gallery 是一个轻量桌面工具，用来把本地图片快速整理进画廊，并批量提交给浏览器执行以图搜图。它保留了原项目成熟的本地桥接与 Chrome 插件能力，但去掉了视频下载、播放和抽帧流程，直接聚焦在图片检索阶段。

## 系统要求

- 操作系统：Windows 10/11、macOS
- Python：3.11+
- 浏览器：Chrome（仅“以图搜图”功能需要）

## 安装与启动

首次启动脚本会自动创建虚拟环境、安装依赖并启动程序。

- Windows：双击根目录 `start.bat`
- macOS：双击根目录 `start.command`

如果 macOS 提示没有执行权限，可先运行：

```bash
chmod +x start.command
```

## 主要功能

- 图片画廊：集中展示已导入图片，支持多选后批量搜图
- 拖拽导入：直接把图片拖进窗口即可加入画廊
- 文件导入：从本地批量选择图片复制进图库
- 打开图库：一键打开图库文件夹，用系统方式管理原图
- 插件联动：调用共享的浏览器插件，把本地图片提交给 Google Lens

默认图库目录位于 `workspace/image_gallery/`。

## 浏览器插件

插件名称：`Local Lens Bridge`

1. 打开 `chrome://extensions/`
2. 开启“开发者模式”
3. 选择“加载已解压的扩展程序”
4. 选择仓库目录 `chrome_extension/`
5. 启动桌面程序
6. 在插件弹窗中将状态切换为“开启”

桥接地址：`http://127.0.0.1:38999`

## 使用方式

1. 把图片拖进窗口，或点击“导入图片”
2. 在画廊中选中想搜的图片；如果不选，则默认提交全部图片
3. 点击“批量以图搜图”
4. 浏览器会自动打开 Google Lens 并开始处理队列

## 常见问题

### 点了“批量以图搜图”没有反应

- 确认 Chrome 扩展 `Local Lens Bridge` 已安装并开启
- 确认 Chrome 当前没有被系统权限拦截
- 查看 `logs/chrome_extension_bridge.log`

### 无法启动

- 查看 `logs/launcher.log`
- 查看 `logs/app.log`

## 第三方依赖与许可证

第三方组件与许可证信息见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。

## 免责声明

本项目仅提供通用技术能力。使用者应自行遵守所在地区法律法规、目标平台条款及内容版权规则。
