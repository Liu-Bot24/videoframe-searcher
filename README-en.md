<div align="center">

# Image Search Gallery

Languages: [简体中文](README.md) · [English](README-en.md)

</div>

Image Search Gallery is a lightweight desktop app for collecting local images into a gallery and sending them in batches to a browser-based reverse-image-search workflow. It keeps the original bridge and Chrome extension integration, while removing the video download, playback, and frame extraction flow.

## System Requirements

- Operating System: Windows 10/11, macOS
- Python: 3.11+
- Browser: Chrome (required only for reverse image search)

## Install and Start

The startup script creates a virtual environment, installs dependencies, and launches the app on first run.

- Windows: double-click `start.bat`
- macOS: double-click `start.command`

If macOS reports that the script is not executable, run:

```bash
chmod +x start.command
```

## What You Can Do

- Image gallery: browse imported images and batch-select them for search
- Drag-and-drop import: drop images directly into the window
- File import: copy local images into the managed gallery
- Open gallery folder: manage the gallery with Finder / Explorer
- Shared plugin flow: submit local images to Google Lens through the shared browser extension

The default gallery directory is `workspace/image_gallery/`.

## Browser Extension

Extension name: `Local Lens Bridge`

1. Open `chrome://extensions/`
2. Enable Developer mode
3. Click Load unpacked
4. Select the repository folder `chrome_extension/`
5. Start the desktop app
6. Turn the extension status On in the popup

Bridge address: `http://127.0.0.1:38999`

## Workflow

1. Drag images into the window, or click Import Images
2. Select the images you want to search; if nothing is selected, the app submits all images in the gallery
3. Click Batch Reverse Search
4. Chrome opens Google Lens and starts processing the queue

## FAQ

### Nothing happens when I click Batch Reverse Search

- Make sure the `Local Lens Bridge` extension is installed and enabled
- Make sure Chrome is not blocked by system permissions
- Check `logs/chrome_extension_bridge.log`

### The app does not start

- Check `logs/launcher.log`
- Check `logs/app.log`

## Third-Party Dependencies and Licenses

For third-party components and license information, see [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

## Disclaimer

This project only provides general technical capabilities. Users should comply with local laws, platform terms, and copyright rules.
