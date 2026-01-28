# ytv-launcher

ytv-launcher is a lightweight YouTube TV launcher that pairs a simple web UI with a small aiohttp WebSocket backend. The frontend lets you enter a YouTube URL and send it to the server, which validates the link and forwards an `adb` intent to launch the YouTube TV app on a connected Android TV/Google TV device, streaming logs back to the browser in real time.
