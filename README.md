<p align="center">
  <img src="logo.png" alt="Project Logo" width="200"/>
</p>

# Bambu Timelapse Downloader

A Python tool to automate downloading, upscaling, and making streamable timelapse videos from a Bambu 3D printer via FTPS.

---

## Features

- **Secure Download:** Downloads timelapse videos from your Bambu printer using FTPS.
- **Flexible Selection:** Download the latest or all available timelapse videos.
- **Watch Mode:** Continuously checks for new videos every 60 seconds and downloads them automatically.
- **Automatic Conversion:** By default, upscales videos to 1080p and makes them streamable using ffmpeg with NVIDIA GPU acceleration, or CPU-only mode with `--no-gpu`.
- **Clean-Up:** Deletes remote files after download and deletes original files after successful conversion.
- **Configurable:** Printer credentials are stored in a config file, not in the script.
- **Organized Output:** Stores videos in a `timelapse` subfolder by default.

---

## Requirements

- Python 3.7+
- tqdm
- ffmpeg (with NVIDIA GPU support and `hevc_nvenc`) or just CPU (see `--no-gpu` option)
- Bambu printer with FTP access

---

## Installation

Install Python dependencies with pip:
```bash
pip install -r requirements.txt
```

You also need ffmpeg with NVIDIA GPU support (see [ffmpeg docs](https://ffmpeg.org/)).

---

## Setup

1. **Clone this repository.**

2. **Create a config file:**
   - Copy `config.json_template` to `config.json` and fill in your printer details:
     ```json
     {
       "printer_ip": "192.168.1.123",
       "access_code": "YOUR_ACCESS_CODE"
     }
     ```

3. **Ensure ffmpeg is installed with NVIDIA GPU support.**

---

## Usage

```bash
python get_timelapse.py [options]
```

### Options

- `--last`  
  Download only the latest timelapse video (default if no option given).

- `--all`  
  Download all available timelapse videos.

- `--out <folder>`  
  Output directory to save downloaded videos. Default: `./timelapse`

- `--do-not-delete`  
  Do not delete remote file(s) after download (ignored in `--watch` mode).

- `--watch`  
  Continuously check for new timelapse files every 60 seconds and download them.

- `--no-make-streamable`  
  Do **not** convert videos to streamable 1080p using ffmpeg Default: conversion is ON.

- `--no-gpu`  
  Force CPU-only processing for video conversion (useful if you do not have an NVIDIA GPU; uses libx265 instead of hevc_nvenc).
  
- `--speed`
  Adjust video speed (Use `0.5` for half speed, `2.0` for double speed). Default: `0.33`.


### Example Commands

Download the latest timelapse and make it streamable (default):
```bash
python get_timelapse.py
```

Download all timelapses and keep the originals on the printer:
```bash
python get_timelapse.py --all --do_not_delete
```

Download to a specific folder, convert to streamable, and run in watch mode:
```bash
python get_timelapse.py --all --watch --out /path/to/folder
```

Download without conversion:
```bash
python get_timelapse.py --no-make-streamable
```

Download and convert using CPU only (no NVIDIA GPU required):
```bash
python get_timelapse.py --no-gpu
```

Download and convert the last timelapse, using CPU only, converted video only having 3/10th of the original speed:
```bash
python ../get_timelapse.py --last --no-gpu --speed 0.3
```

---

## ffmpeg Conversion

By default, after download, each video is converted to a streamable 1080p MP4 using your NVIDIA GPU. If you use `--no-gpu`, conversion will use CPU (libx265) instead.

```bash
ffmpeg -y -hwaccel cuda -i input.mp4 -vf scale=1920:1080 -c:v hevc_nvenc -preset p7 -tune hq -b:v 15M -tag:v hvc1 -video_track_timescale 90000 output_streamable.mp4
```

**CPU-only example (with --no-gpu):**
```bash
ffmpeg -y -i input.mp4 -vf scale=1920:1080 -c:v libx265 -preset slow -b:v 15M -tag:v hvc1 -video_track_timescale 90000 output_streamable.mp4
```

The original file is deleted after successful conversion.

---

## Telegram Upload (Optional)

If you want each streamable video to be uploaded automatically to a Telegram channel:

1. Create a Telegram bot and get the bot token.
2. Add the bot to your channel/group and get the channel username (e.g. @your_channel) or chat ID.
3. Add these fields to your `config.json`:
   ```json
   {
     "telegram_bot_token": "YOUR_BOT_TOKEN",
     "telegram_channel_id": "@your_channel_or_chat_id"
   }
   ```

### How to Get Your Telegram Group Chat ID

1. **Invite [@ShowJsonBot](https://t.me/ShowJsonBot) to your group.**
2. **Send any message in the group.**
3. **The bot will reply with the full JSON, including the chat ID.**
4. **Use the value of `"id"` as your `telegram_channel_id` in `config.json`.**

If both fields are present, every converted (streamable) video will be uploaded to your Telegram channel automatically after processing.

If not set, Telegram upload is skipped.

---

## Notes

- Ensure your printer’s FTP server is accessible and credentials are correct (set in `config.json`).
- ffmpeg with NVIDIA GPU support is required for conversion (see [ffmpeg docs](https://ffmpeg.org/)).
- The script creates a `timelapse` folder for output by default.

---

## Support & Donations

If you found this project useful, consider supporting my work with a small donation: [ko-fi.com/yurymonzon](https://ko-fi.com/yurymonzon)
Your support is greatly appreciated!

---

## Attribution

Inspired by [SuiDog’s post on the Bambu Lab Forum](https://forum.bambulab.com/t/connecting-to-the-p1s-ftp-with-python/115179).

---

## License

MIT License

---
