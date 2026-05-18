import ftplib
from ftplib import all_errors
import ssl
import os
import json
from datetime import datetime
from tqdm import tqdm
import argparse
import time
import subprocess
from telegram import Bot
from telegram.error import TelegramError
import asyncio
import re
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


# --- Optional HTTP status endpoint (enable with --status-port) ---
# Lets external monitoring (Uptime Kuma, Prometheus blackbox, etc.) probe
# whether the watcher is healthy. Disabled unless --status-port is passed.

_STATE_LOCK = threading.Lock()
_STATE = {
    "state": "starting",   # starting | idle | working | error
    "last_check": None,    # ISO timestamp of last completed cycle
    "last_success": None,  # ISO timestamp of last cycle without errors
    "last_error": None,    # last error message
    "current_file": None,  # name of file currently being processed
}


def _set_state(**kwargs):
    with _STATE_LOCK:
        _STATE.update(kwargs)


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


class _StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") not in ("", "/status", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        with _STATE_LOCK:
            snapshot = dict(_STATE)
        body = json.dumps(snapshot).encode()
        # 503 when the last cycle errored, so a plain HTTP status-code
        # monitor is sufficient (no JSON parsing required).
        code = 503 if snapshot["state"] == "error" else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress access logs — the monitoring poll would spam stdout.
        pass


def _start_status_server(port):
    server = HTTPServer(("0.0.0.0", port), _StatusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Status server listening on :{port}")


def check_ffmpeg_dependencies():
    """Check if ffmpeg and ffprobe are available in the system PATH."""
    ffmpeg_path = shutil.which('ffmpeg')
    ffprobe_path = shutil.which('ffprobe')
    
    if not ffmpeg_path or not ffprobe_path:
        print("Critical Dependency Error: FFmpeg tools not found")
        if not ffmpeg_path:
            print("- ffmpeg is not found in system PATH or script directory")
        if not ffprobe_path:
            print("- ffprobe is not found in system PATH or script directory")
        print("Please install FFmpeg and ensure it's in your system PATH or in the same directory as this script.")
        sys.exit(1)
    
    print(f"Dependencies found:\n- ffmpeg: {ffmpeg_path}\n- ffprobe: {ffprobe_path}")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)
PRINTER_IP = config.get('printer_ip')
ACCESS_CODE = config.get('access_code')

# Check FFmpeg dependencies on script launch
check_ffmpeg_dependencies()

class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS subclass that automatically wraps sockets in SSL to support implicit FTPS."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        """Return the socket."""
        return self._sock

    @sock.setter
    def sock(self, value):
        """When modifying the socket, ensure that it is ssl wrapped."""
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

def parse_ftp_listing(line):
    """Parse a line from an FTP LIST command."""
    parts = line.split(maxsplit=8)
    if len(parts) < 9:
        return None
    return {
        'permissions': parts[0],
        'links': int(parts[1]),
        'owner': parts[2],
        'group': parts[3],
        'size': int(parts[4]),
        'month': parts[5],
        'day': int(parts[6]),
        'time_or_year': parts[7],
        'name': parts[8]
    }

def get_base_name(filename):
    return filename.rsplit('.', 1)[0]

def parse_date(item):
    """Parse the date and time from the FTP listing item."""
    try:
        # Use the current year as default to avoid deprecation warning
        current_year = datetime.now().year
        date_str = f"{item['month']} {item['day']} {item['time_or_year']}"
        
        # Try parsing with current year
        parsed_date = datetime.strptime(f"{current_year} {date_str}", "%Y %b %d %H:%M")
        
        # If the parsed date is in the future, use previous year
        if parsed_date > datetime.now():
            parsed_date = datetime.strptime(f"{current_year - 1} {date_str}", "%Y %b %d %H:%M")
        
        return parsed_date
    except ValueError:
        return None

def extract_datetime_from_filename(filename):
    # Matches video_YYYY-MM-DD_HH-MM-SS.*
    m = re.search(r'video_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})', filename)
    if m:
        date_str = m.group(1)
        time_str = m.group(2).replace('-', ':')
        return f"Timelapse: {date_str} {time_str}"
    return "Timelapse"

# Utility function to get video duration using ffprobe
def get_video_duration(filename):
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', filename
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Could not determine video duration for {filename}: {e}")
        return 999.0

async def try_telegram_upload(config, file_path, caption=None):
    bot_token = config.get('telegram_bot_token')
    channel_id = config.get('telegram_channel_id')
    if not bot_token or not channel_id:
        print("Telegram upload skipped: Missing bot token or channel ID")
        return False
    
    # Validate file exists and is not empty
    if not os.path.exists(file_path):
        print(f"Error: File not found - {file_path}")
        return False
    
    if os.path.getsize(file_path) == 0:
        print(f"Error: File is empty - {file_path}")
        return False
    
    try:
        bot = Bot(token=bot_token)
        with open(file_path, 'rb') as vid:
            await bot.send_video(chat_id=channel_id, video=vid, supports_streaming=True, caption=caption)
        print(f'Successfully uploaded to Telegram: {channel_id}')
        return True
    except TelegramError as e:
        print(f'Failed to upload to Telegram: {e}')
        return False

def main():
    parser = argparse.ArgumentParser(description="Download timelapse videos via FTP.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--last', action='store_true', help='Download the latest timelapse video (default)')
    group.add_argument('--all', action='store_true', help='Download all matching timelapse videos')
    parser.add_argument('--do-not-delete', action='store_true', help="Do not delete remote file(s) after download")
    default_timelapse_dir = os.path.join(os.path.dirname(__file__), 'timelapse')
    parser.add_argument('--out', default=default_timelapse_dir, help='Output folder to save videos (default: ./timelapse)')
    parser.add_argument('--watch', action='store_true', help='Continuously check every 60s and download new files')
    parser.add_argument('--no-make-streamable', action='store_true', help='Do NOT use ffmpeg+NVIDIA to upscale to 1080p and make streamable (default is ON)')
    parser.add_argument('--upscale', action='store_true', help='Upscale to 1080p when creating streamable file (uses GPU if available). Off by default.')
    parser.add_argument('--keep-after-upload', action='store_true', help='Keep streamable file after Telegram upload (default: delete after upload)')
    parser.add_argument('--no-gpu', action='store_true', help='Force CPU-only processing (no NVIDIA GPU required)')
    parser.add_argument('--speed', type=float, default=0.3, help='Adjust video speed (e.g., 0.5 for half speed, 2.0 for double speed). Default is 0.3 (slower speed).')
    parser.add_argument('--test', action='store_true', help='Run test mode: process and upload test_video.avi')
    parser.add_argument('--status-port', type=int, default=None, help='Expose an HTTP status endpoint on this port (returns JSON; 200 normally, 503 on error). Useful with --watch for external monitoring (Uptime Kuma, etc.).')
    args = parser.parse_args()

    if args.status_port:
        _start_status_server(args.status_port)

    # Test mode implementation
    if args.test:
        # Specific test video file
        script_dir = os.path.dirname(__file__)
        test_video = os.path.join(script_dir, 'test_video.avi')
        
        if not os.path.exists(test_video):
            print(f'Test video not found: {test_video}')
            sys.exit(1)
        
        print(f'Testing with video: {test_video}')
        
        # Prepare output directory
        out_dir = args.out
        os.makedirs(out_dir, exist_ok=True)
        
        # Process video
        streamable_filename = os.path.splitext(test_video)[0] + '_streamable.mp4'
        
        # Calculate original FPS
        original_fps = float(subprocess.check_output([
            'ffprobe', '-v', 'error', 
            '-select_streams', 'v:0', 
            '-count_packets', 
            '-show_entries', 'stream=r_frame_rate', 
            '-of', 'csv=p=0', 
            test_video
        ], text=True).strip().split('/')[0])
        
        # Adjust frame selection to maintain video quality while reducing frame count
        target_fps = max(1, original_fps * args.speed)
        
        if args.no_gpu:
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-i', test_video,
                '-vf', f'fps={target_fps},scale=1920:1080',
                '-c:v', 'libx265', '-preset', 'slow', '-b:v', '5M',
                '-tag:v', 'hvc1', '-video_track_timescale', '90000',
                streamable_filename
            ]
        else:
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-hwaccel', 'cuda', '-i', test_video,
                '-vf', f'fps={target_fps}',
                '-c:v', 'hevc_nvenc', '-preset', 'p7', '-tune', 'hq', '-b:v', '5M',
                '-tag:v', 'hvc1', '-video_track_timescale', '90000',
                streamable_filename
            ]
        
        try:
            subprocess.run(ffmpeg_cmd, check=True)
            print(f'Created streamable video at {streamable_filename} (speed: {args.speed}x)')
            
            # Attempt Telegram upload
            caption = f'Test Video: {os.path.basename(test_video)} (Speed: {args.speed}x)'
            tg_success = asyncio.run(try_telegram_upload(config, streamable_filename, caption=caption))
            
            # Clean up
            if os.path.exists(streamable_filename):
                os.remove(streamable_filename)
            
            sys.exit(0 if tg_success else 1)
        
        except subprocess.CalledProcessError as e:
            print(f'Test mode failed: {e}')
            sys.exit(1)

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    async def download_and_process():
        _set_state(state="working", last_error=None)
        ftp = None
        try:
            ftp = ImplicitFTP_TLS()
            ftp.set_pasv(True)
            print('Connecting...')
            try:
                ftp.connect(host=PRINTER_IP, port=990, timeout=15, source_address=None)
                ftp.login('bblp', ACCESS_CODE)
                ftp.prot_p()
            except OSError as e:
                msg = f"Network error during FTP connection: {e}"
                print(msg)
                _set_state(state="error", last_error=msg, last_check=_now_iso())
                return False
            except all_errors as ex:
                msg = f"FTP error during connection: {ex}"
                print(msg)
                _set_state(state="error", last_error=msg, last_check=_now_iso())
                return False

            tldirlist = []
            tltndirlist = []
            try:
                ftp.cwd('/timelapse')
                ftp.retrlines('LIST', tldirlist.append)
                tldirlist = [parse_ftp_listing(line) for line in tldirlist if parse_ftp_listing(line)]
                ftp.cwd('/timelapse/thumbnail')
                ftp.retrlines('LIST', tltndirlist.append)
                tltndirlist = [parse_ftp_listing(line) for line in tltndirlist if parse_ftp_listing(line)]
            except all_errors as ex:
                msg = f"FTP error during directory listing: {ex}"
                print(msg)
                _set_state(state="error", last_error=msg, last_check=_now_iso())
                return False

            tldirlist_dict = {get_base_name(item['name']): item for item in tldirlist}
            tltndirlist_set = {get_base_name(item['name']) for item in tltndirlist}
            matching_files = [tldirlist_dict[base_name] for base_name in tldirlist_dict if base_name in tltndirlist_set]

            if not matching_files:
                print('No matching files found.')
                _set_state(state="idle", current_file=None, last_check=_now_iso(), last_success=_now_iso())
                return False

            matching_files.sort(key=lambda x: parse_date(x) or datetime.min, reverse=True)
            files_to_download = [matching_files[0]] if not args.all else matching_files

            total_size = sum(item["size"] for item in files_to_download)
            if args.all and len(files_to_download) > 1:
                total_pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc='Total Progress')
            else:
                total_pbar = None

            for item in files_to_download:
                print(f'Processing: {item["name"]}')
                _set_state(current_file=item["name"])
                should_delete_remote_file = True
                local_filename = os.path.join(out_dir, item["name"])
                file_size = item["size"]
                try:
                    with open(local_filename, 'wb') as f:
                        with tqdm(total=file_size, unit='B', unit_scale=True, desc=f"Downloading {item['name']}") as pbar:
                            def callback(data):
                                f.write(data)
                                pbar.update(len(data))
                                if total_pbar:
                                    total_pbar.update(len(data))
                            try:
                                ftp.retrbinary(f'RETR /timelapse/{item["name"]}', callback)
                            except all_errors as ex:
                                print(f"FTP error during file download: {ex}")
                                continue
                    print(f'File downloaded: {local_filename}')
                except Exception as e:
                    print(f"Error writing file {local_filename}: {e}")
                    continue

                # Check video duration before any processing
                try:
                    duration = get_video_duration(local_filename)
                except Exception as e:
                    print(f"Error getting video duration: {e}")
                    duration = 0

                if duration < 1.0:
                    print(f"Skipping processing: {local_filename} is too short ({duration:.2f}s)")
                    print(f'Local file retained: {local_filename}')
                    should_delete_remote_file = True
                    upload_filename = None
                    short_file_skipped = True
                else:
                    short_file_skipped = False

                video_file_ftp_path = f'/timelapse/{item["name"]}'
                
                # Delete remote files by default; use --do-not-delete to prevent deletion.
                if args.do_not_delete:
                    deleted_video_successfully = False
                    should_delete_remote_file = False
                    
                # Attempt to delete remote file if conditions are met
                if should_delete_remote_file:
                    try:
                        ftp.delete(video_file_ftp_path)
                        print(f'Remote file deleted: {video_file_ftp_path}')
                        deleted_video_successfully = True
                    except all_errors as e:
                        print(f'Failed to delete remote file {video_file_ftp_path}: {e}\n')
                        deleted_video_successfully = False
                else:
                    print(f'Remote file retained: {video_file_ftp_path}')
                    deleted_video_successfully = False

                if deleted_video_successfully:
                    video_base_name = get_base_name(item['name'])
                    thumbnail_to_delete_full_name = None
                    for tn_item_detail in tltndirlist:
                        if get_base_name(tn_item_detail['name']) == video_base_name:
                            thumbnail_to_delete_full_name = tn_item_detail['name']
                            break
                    
                    if thumbnail_to_delete_full_name:
                        thumbnail_ftp_path = f'/timelapse/thumbnail/{thumbnail_to_delete_full_name}'
                        try:
                            ftp.delete(thumbnail_ftp_path)
                            print(f'Remote thumbnail deleted: {thumbnail_ftp_path}\n')
                        except all_errors as e:
                            print(f'Failed to delete remote thumbnail {thumbnail_ftp_path}: {e}\n')
                    else:
                        print(f'No corresponding remote thumbnail found for base name {video_base_name} to delete.\n')

                if short_file_skipped:
                    continue

                streamable_filename = None
                upload_filename = local_filename  # Default to original file

                if not args.no_make_streamable:
                    scale_filter = ',scale=1920:1080' if args.upscale else ''
                    streamable_filename = os.path.splitext(local_filename)[0] + '_streamable.mp4'
                    try:
                        original_fps = float(subprocess.check_output([
                            'ffprobe', '-v', 'error',
                            '-select_streams', 'v:0',
                            '-count_packets',
                            '-show_entries', 'stream=r_frame_rate',
                            '-of', 'csv=p=0',
                            local_filename
                        ], text=True).strip().split('/')[0])
                        target_fps = max(1, original_fps * args.speed)
                        if args.no_gpu:
                            ffmpeg_cmd = [
                                'ffmpeg', '-y', '-i', local_filename,
                                '-vf', f'fps={target_fps}{scale_filter}',
                                '-c:v', 'libx265', '-preset', 'slow', '-b:v', '5M',
                                '-tag:v', 'hvc1', '-video_track_timescale', '90000',
                                streamable_filename
                            ]
                        else:
                            ffmpeg_cmd = [
                                'ffmpeg', '-y', '-hwaccel', 'cuda', '-i', local_filename,
                                '-vf', f'fps={target_fps}{scale_filter}',
                                '-c:v', 'hevc_nvenc', '-preset', 'p7', '-tune', 'hq', '-b:v', '5M',
                                '-tag:v', 'hvc1', '-video_track_timescale', '90000',
                                streamable_filename
                            ]
                        print(f'Running ffmpeg to create streamable: {streamable_filename}')
                        subprocess.run(ffmpeg_cmd, check=True)
                        print(f'Streamable file created: {streamable_filename}')
                        # Match the README's "deletes original files after successful
                        # conversion" promise — drop the source .avi as soon as the
                        # encoded .mp4 exists, so users without Telegram configured
                        # don't keep ~3x disk per timelapse.
                        try:
                            os.remove(local_filename)
                            print(f'Original deleted after encode: {local_filename}')
                        except OSError as e:
                            print(f'Could not delete original {local_filename}: {e}')
                        max_telegram_size = 49 * 1024 * 1024  # 49 MB
                        file_size = os.path.getsize(streamable_filename)
                        if file_size > max_telegram_size:
                            print(f'WARNING: Streamable file {streamable_filename} ({file_size / (1024*1024):.2f}MB) is too large for Telegram (limit ~49MB).')
                            print(f'Skipping upload for this file. Both {streamable_filename} and original {local_filename} will be kept for manual handling.')
                            streamable_filename = None
                        else:
                            upload_filename = streamable_filename
                    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
                        print(f'Error processing video: {e}')
                        streamable_filename = None
                    except Exception as e:
                        print(f'Unexpected error during streamable creation: {e}')
                        streamable_filename = None

                # Attempt Telegram upload
                if upload_filename:
                    caption = extract_datetime_from_filename(os.path.basename(local_filename))
                    try:
                        upload_success = await try_telegram_upload(config, upload_filename, caption=caption)
                    except Exception as e:
                        print(f"Error during Telegram upload: {e}")
                        upload_success = False
                    
                    # Clean up files after successful upload
                    if upload_success:
                        if not args.keep_after_upload:
                            try:
                                if streamable_filename and os.path.exists(streamable_filename):
                                    os.remove(streamable_filename)
                                if os.path.exists(local_filename):
                                    os.remove(local_filename)
                            except Exception as e:
                                print(f"Error during cleanup: {e}")
                        print(f'Uploaded and cleaned up: {upload_filename}')

            if total_pbar:
                total_pbar.close()

            _set_state(state="idle", current_file=None, last_check=_now_iso(), last_success=_now_iso())

        except Exception as ex:
            msg = f"General error in download_and_process: {ex}"
            print(msg)
            _set_state(state="error", current_file=None, last_error=msg, last_check=_now_iso())
            return False
        finally:
            if ftp is not None:
                try:
                    if ftp.sock:
                        ftp.quit()
                        print('Disconnected. Enjoy =D')
                    else:
                        print('FTP connection was not fully established or already closed.')
                except all_errors as e:
                    print(f'Error during FTP quit/close: {e}. Connection might have already been terminated.')
                except Exception as e:
                    print(f'Unexpected error during FTP quit/close: {e}')
                if hasattr(ftp, '_sock') and ftp._sock is not None:
                    try:
                        ftp.close()
                    except all_errors:
                        pass
                    except Exception:
                        pass
        return True

    if args.watch:
        print('Entering watch mode. Checking for new files every 60 seconds...')
        while True:
            processed_files = asyncio.run(download_and_process())
            if not processed_files:
                time.sleep(60)
    else:
        try:
            asyncio.run(download_and_process())
        except Exception as e:
            print(f"Error occurred: {e}")

if __name__ == "__main__":
    main()
