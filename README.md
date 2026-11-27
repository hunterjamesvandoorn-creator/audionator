# Audionator

The GitHub Pages site is a static UI preview. Raw UDP is not available to web pages, so audio sharing runs in the separate Windows server and client apps.

The standalone Windows `.exe` files bundle Python and the application packages, so users do not need to install Python or run `pip install`. The following requirements apply only when running from Python source or building the apps. Video playback uses the Microsoft Edge WebView2 Runtime, which may need to be installed separately on older Windows systems.

## Python source requirements

- Python 3.10 or later
- `pygame` for MP3 playback and `mutagen` for track duration and seeking in the client
- `pywebview` and Microsoft Edge WebView2 Runtime for the desktop client window and built-in video player
- `imageio-ffmpeg` (bundled FFmpeg) for preparing MP4 files into adaptive video segments on the server

## Build standalone Windows apps

On the Windows development computer, double-click `build-windows.bat` in this project folder:

It builds two standalone apps in `dist`:

- `Audionator.exe` is the client. Copy it to a receiving computer and double-click it; that computer does not need Python installed. The app window and built-in video player use Microsoft Edge WebView2.
- `AudionatorServer.exe` is the media server. Put it beside a `music` folder containing MP3 files and a `videos` folder containing MP4 files, then double-click it. Its console window stays open while the server is running; close that window to stop the server. Audio and video both use UDP port `5005`.

The server computer does not need Python installed either. Windows Firewall may ask to allow the server; permit it on **Private networks only** for UDP port `5005`. The server and client should be used only on a trusted local network.

Received MP3 files are saved under `%LOCALAPPDATA%\Audionator\received`. Use **Open folder** in the client to find them.

To run the client from Python instead of using the standalone app, install its dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Run from Python

1. Put `.mp3` files in the `music` folder and `.mp4` files in the `videos` folder beside `server.py`.
2. Start the server:

   ```powershell
   python server.py --udp --library .\music --video-library .\videos
   ```

3. In a second terminal, start the receiving client:

   ```powershell
   python client.py
   ```

4. Enter the server computer's address (`127.0.0.1` only when client and server are on the same computer) and select **Connect**. The client remembers the last server on that device. Browse **Music** and play a track, or open **Videos** and choose a video to play inline in the same window. Video starts after a short buffer and continues receiving over UDP.

Received MP3 tracks are saved to `received`. MP4 files remain on the server.

Video is prepared on the server as an adaptive HLS-style ladder of fragmented MP4 segments (6 seconds each) at 720p, 480p, and 360p. Preparation starts in the background when the server starts, so the first play of a video is usually quick; prepared levels are cached and reused on later runs. The client selects a level for each segment automatically based on throughput and packet loss, and the **Quality** menu in the player lets you lock a level or return to **Auto**. A traffic meter shows the recent receive rate. Video packets are retransmitted if lost, and playback pauses to buffer if the connection falls behind.

## Use another device on your local network

Start the server on the computer that has the MP3 and MP4 files, binding it to the network:

```powershell
python server.py --udp --host 0.0.0.0 --port 5005 --library .\music --video-library .\videos
```

If Windows Firewall prompts you, allow the server on **Private networks only** for UDP port `5005`. All media packets and acknowledgements use this same port. In the client, enter the server computer's local IPv4 address (find it with `ipconfig`) and port `5005`.

MP3 files transfer as numbered UDP chunks, retry missing chunks, and verify the completed file with SHA-256 before playback. The client includes a seek slider with elapsed and total time. Audio playback begins after the whole track arrives. MP4 video is transcoded by FFmpeg into the three quality levels above and each segment is requested over UDP as a reliable transfer; the browser player (Media Source Extensions) begins after a short buffer and pauses if it needs more data. Prepared segments are cached under `%LOCALAPPDATA%\AudionatorServer\video_cache` on Windows (`video_cache` beside the source code elsewhere). Each video uses roughly three renditions of its size, and startup encoding time grows with the library. Delete that folder to reclaim space; it is rebuilt on demand. The prototype has no authentication or encryption: use it only on a trusted local network, not the public internet.

## UI preview

Run `python server.py` and open [http://127.0.0.1:8000](http://127.0.0.1:8000) to view the static browser preview.
