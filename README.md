# Audionator

The GitHub Pages site is a static UI preview. Raw UDP is not available to web pages, so the working audio prototype uses a Python sender and a small desktop receiving client.

## Requirements

- Python 3.10 or later
- `pygame` for MP3 playback in the client

Install the client dependency:

```powershell
python -m pip install -r requirements.txt
```

## Run a server and client on one computer

1. Put `.mp3` files in the `music` folder beside `server.py`.
2. Start the server:

   ```powershell
   python server.py --udp --library .\music
   ```

3. In a second terminal, start the receiving client:

   ```powershell
   python client.py
   ```

4. Leave the client server address as `127.0.0.1`, select **Connect**, choose a track, and select **Receive & play**.

Received tracks are saved to `received`.

## Use another device on your local network

Start the server on the computer that has the MP3 files, binding it to the network:

```powershell
python server.py --udp --host 0.0.0.0 --port 5005 --library .\music
```

If Windows Firewall prompts you, allow Python on **Private networks only**. In the client, enter the server computer's local IPv4 address (find it with `ipconfig`) and port `5005`.

The prototype transfers MP3 files as numbered UDP chunks, retries missing chunks, checks the completed file with SHA-256, then plays it. Playback begins after the whole file has arrived; this is file delivery over UDP, not live audio streaming. The UDP prototype has no authentication or encryption: use it only on a trusted local network, not the public internet.

## UI preview

Run `python server.py` and open [http://127.0.0.1:8000](http://127.0.0.1:8000) to view the static browser preview.
