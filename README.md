# Audionator

The GitHub Pages site is a static UI preview. Raw UDP is not available to web pages, so audio sharing runs in the separate Windows server and client apps.

The standalone Windows `.exe` files bundle Python and the application packages, so users do not need to install Python or run `pip install`. The following requirements apply only when running from Python source or building the apps. Video playback uses the Microsoft Edge WebView2 Runtime, which may need to be installed separately on older Windows systems.

## Python source requirements

- Python 3.10 or later
- `pygame` for MP3 playback and `mutagen` for track duration and seeking in the client
- `pywebview` and Microsoft Edge WebView2 Runtime for the desktop video player
- `imageio-ffmpeg` for preparing MP4 files for progressive playback

## Build standalone Windows apps

On the Windows development computer, double-click `build-windows.bat` in this project folder:

It builds two standalone apps in `dist`:

- `Audionator.exe` is the client. Copy it to a receiving computer and double-click it; that computer does not need Python installed. Video playback requires the Microsoft Edge WebView2 Runtime.
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

4. Enter the server computer's address (`127.0.0.1` only when client and server are on the same computer) and select **Connect**. Choose a song in the **Audio** tab and select **Receive & play**, or choose a video in the **Videos** tab and select **Play video**. Video starts after a short buffer and continues receiving over UDP.

Received MP3 tracks are saved to `received`. MP4 files remain on the server. The first play prepares a fast-start copy without re-encoding; video packets are retransmitted if lost, and playback pauses to buffer if the connection falls behind.

## Use another device on your local network

Start the server on the computer that has the MP3 and MP4 files, binding it to the network:

```powershell
python server.py --udp --host 0.0.0.0 --port 5005 --library .\music --video-library .\videos
```

If Windows Firewall prompts you, allow the server on **Private networks only** for UDP port `5005`. All media packets and acknowledgements use this same port. In the client, enter the server computer's local IPv4 address (find it with `ipconfig`) and port `5005`.

MP3 files transfer as numbered UDP chunks, retry missing chunks, and verify the completed file with SHA-256 before playback. The client includes a seek slider with elapsed and total time. Audio playback begins after the whole track arrives. MP4 video is remuxed by FFmpeg to put its metadata at the start, then sent in reliable UDP chunks; the video player can begin after up to 2 MB has buffered and pauses if it needs more data. The prototype has no authentication or encryption: use it only on a trusted local network, not the public internet.

## UI preview

Run `python server.py` and open [http://127.0.0.1:8000](http://127.0.0.1:8000) to view the static browser preview.
