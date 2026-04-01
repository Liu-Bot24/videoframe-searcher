<div align="center">

# VideoFrame Searcher

Languages: [简体中文](README.md) · [English](README-en.md)

</div>

VideoFrame Searcher is a Windows desktop tool used to quickly extract screenshots from online or local videos and organize subsequent search processes.

## System Requirements

- Operating System: Windows 10/11
- Python: 3.11+
- Browser: Chrome (required only for the "Search by Image" feature)

## Installation & Startup

### One-Click Startup (Recommended)

Double-click `start.bat` in the root directory:

1. Detects Python
2. Creates `.venv`
3. Installs dependencies
4. Starts the program

### Command Line Startup

```bash
python run.py
```

## Main Features

- **Link Parsing**: Fetches metadata such as title, duration, cover, and source based on `yt-dlp`.
- **Video Downloading**: Supports quality priority, proxies, Cookies, extra parameters, and multi-strategy fallbacks.
- **Local Import**: Copies local videos to the project directory and directly enters the frame extraction process.
- **Auto Frame Extraction**: Extracts frames at fixed intervals, with an option to clean up historical screenshots for the video.
- **Manual Frame Extraction**: Supports marking multiple timestamps; results can be merged with interval extraction outputs.
- **Video Playback**: Drag progress, frame-by-frame via left/right keys, screenshot current position, batch screenshot at marked points.
- **Screenshot Gallery**: Paginated browsing, batch selection, delete screenshots, right-click delete, open screenshot directory.
- **Plugin Integration**: After selecting a screenshot, submit it to the Chrome extension to perform a reverse image search.
- **Project Management**: Historical project list, thumbnail preview, title scrolling display, project deletion.

## Browser Extension (Search by Image)

1. Open `chrome://extensions/`
2. Enable "Developer mode"
3. Select "Load unpacked"
4. Choose the repository directory `chrome_extension/`
5. Start the desktop program
6. Toggle the status to "On" in the extension popup

Bridge Address: `http://127.0.0.1:38999`

## Usage Workflow

1. Enter the video URL in the "Collection Workbench" and click "Parse Metadata".
2. Select quality priority and click "Start Download", or directly upload a local video.
3. Set the frame extraction interval and manual time points, then execute "Start Extraction".
4. Locate frames in "Video Playback" and execute screenshot current / screenshot marked points / batch screenshot.
5. Filter, delete, or execute "Search by Image" in the "Screenshot Gallery".

## Configuration

The settings page supports the following configurations:

- Workspace directory
- Cookie authorization (Browser or cookies.txt)
- HTTP proxy
- Download format expression
- Merge output format
- yt-dlp extra parameters
- yt-dlp force update

## Directory Structure

```text
.
├─ start.bat
├─ run.py
├─ chrome_extension/
├─ videoframe_searcher/
├─ workspace/                  # Project workspace directory
├─ logs/
│  ├─ app.log
│  ├─ launcher.log
│  └─ chrome_extension_bridge.log
└─ config/settings.json
```

## FAQ

### Cannot Start

Check first:

- `logs/launcher.log`
- `logs/app.log`

### Cannot Parse or Download

Check the following:

- Link accessibility
- Whether the login state and Cookie are valid
- Whether the proxy settings are working
- Whether yt-dlp is updated to the latest version

### Search by Image Unresponsive

- Ensure the Chrome extension is installed and enabled
- Ensure the bridge service is online

## Third-Party Dependencies and Licenses

For third-party components and license information, see [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

## Disclaimer

This project only provides general technical capabilities. Users should comply with local laws and regulations, target platform terms, and content copyright rules.