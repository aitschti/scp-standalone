# Stripchat Standalone Proxy

This project provides a standalone proxy server for streaming from the Stripchat platform. The proxy allows users to fetch streams by specifying a username from the command line or dynamically via HTTP requests. It automatically selects an available port if none is specified and logs the URL to be used for streaming.

## Files

- **scp-standalone.py**: The main implementation of the standalone proxy server. This file contains the logic to start the proxy, handle streaming requests, and manage the decryption of playlist names using a key stored in `key.txt`.

- **key.txt**: A text file that stores the decryption key required for processing encrypted playlist file urls. The proxy reads this file to obtain the necessary key for decrypting playlist data. I CANNOT PROVIDE THE KEY!

## Usage

1. **Install Dependencies**: Ensure you have Python installed along with any required libraries. You may need to install additional packages depending on your environment.

2. **Set Up the Key**: Create a `key.txt` file in the project directory and add your decryption key. This key is essential for the proxy to function correctly with encrypted playlist files.

3. **Run the Proxy**:
   - **With a Default Username**: Open a terminal and navigate to the project directory. Start the proxy with a username to set a default stream:
  
     ```cmd
     python scp-standalone.py <username> [--port <port>] [--host <host>]
     ```
     
     Replace `<username>` with the desired username to fetch streams. Optionally, specify a port with `--port` (default: auto-select) and host with `--host` (default: 127.0.0.1). The proxy will fetch the stream URL at startup and serve it as the default for requests to the root path (`/`).

   - **Without a Default Username**: Start the proxy without a username to enable dynamic requests:

     ```cmd
     python scp-standalone.py [--port <port>] [--host <host>]
     ```

     In this mode, the proxy waits for incoming requests in the format `http://<host>:<port>/<username>` to fetch and serve streams on-demand. Requests to the root path (`/`) will return an error if no default is set.

4. **Access the Stream**:
   - Once the proxy is running, it will log the URL(s) for access.
   - For default streams (if username provided at startup): Use `http://<host>:<port>/` or the logged proxy URL.
   - For dynamic streams: Use `http://<host>:<port>/<username>` to fetch and stream for that username.
   - The proxy supports multiple concurrent connections via threading.

## Features

- Automatically selects an available port if none is specified.
- Logs the streaming URL(s) for easy access.
- Reads from `key.txt` for decryption of playlist names.
- Supports dynamic username requests via `http://<host>:<port>/<username>` for on-demand streaming.
- Allows setting a default stream at startup for convenience.
- Supports multiple concurrent connections for simultaneous users.
- Caches fetched M3U8 URLs per username (with a 5-minute TTL) to reduce API calls.

## License

For educational purposes only. Do not use for any commercial activities.
