# Stripchat Standalone Proxy

**DEC '25 UPDATE: AUTOMATIC KEY FETCHING IS BROKEN ATM, NOTHING I CAN DO. OLD KEY REMAINS VALID FOR NOW. RETRY CONNECTION IF IT FAILS, THEY SWITCH AROUND OTHER KEYS**

This project provides a standalone proxy server for streaming from the Stripchat platform. The proxy allows users to fetch streams by specifying a username from the command line or dynamically via HTTP requests. It automatically selects an available port if none is specified and logs the URL to be used for streaming. It also handles the decoding of the newly added scrambling of playlist file urls by Stripchat.

## Files

- **scp-standalone.py**: The main implementation of the standalone proxy server. This file contains the logic to start the proxy, handle streaming requests, and manage the decryption of playlist file urls by fetching a new one if decoding fails or the `key.txt` file is missing.

- **key.txt**: A text file that stores the key required for processing encrypted playlist file urls. Gets created/updated on first request if missing or key is invalid.

## Quick Start / Usage

1. **Install Dependencies**: Ensure you have Python installed along with any required libraries. You may need to install additional packages depending on your environment.

2. **Run the Proxy**:
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

   - **Serve best quality only**: To force the proxy to use the best quality playlist instead of the variants playlist, add the `--best` flag:

     ```cmd
     python scp-standalone.py <username> --best
     ```

4. **Access the Stream**:
   - Once the proxy is running, it will log the URL(s) for access.
   - For default streams (if username provided at startup): Use `http://<host>:<port>/` or the logged proxy URL.
   - For dynamic streams: Use `http://<host>:<port>/<username>` to fetch and stream for that username.
   - The proxy supports multiple concurrent connections.
   - Use in your app of choice that can handle HLS (HTTP Live Streaming) to access the stream.

5. **Compatibility**:
   - Some apps may need the .m3u8 file extension in the requested URL to function correctly. You can append `.m3u8` to the end of the stream URL after the username if needed.

6. **Verbosity**:
   - By default, the proxy runs in a non-verbose mode, suppressing standard HTTP request logs, only showing essential information.
   - To enable verbose logging, start the proxy with the `--verbose` flag.

## All Command Arguments

The proxy accepts the following command-line arguments:

- `<username>` (positional, optional): The username of the streamer to set as the default stream at startup. If provided, the proxy fetches the stream URL immediately and serves it for requests to the root path (`/`). If omitted, the proxy operates in dynamic mode, waiting for username-specific requests.

- `--port <port>` (optional, integer, default: 0): Specifies the port number for the proxy server to listen on. If set to 0 or omitted, the proxy automatically selects an available port.

- `--host <host>` (optional, string, default: '127.0.0.1'): Specifies the host address for the proxy server to bind to. Defaults to localhost (127.0.0.1) for local access.

- `--proxy <proxy_url>` (optional, string): Specifies a proxy URL to use for all HTTP/HTTPS requests (e.g., `socks4://ip:port` or `http://ip:port`). This is optional and allows routing traffic through a proxy server. Requires the `PySocks` library for SOCKS proxies.

- `--best` (optional, flag): Forces the proxy to use the best quality playlist instead of the variants playlist. This serves a single high-quality stream directly, bypassing quality selection options. This may improve quality for some players (no switching).

- `--verbose` (optional, flag): Enables verbose logging, which includes debug messages from the proxy handler in addition to standard info logs. Useful for troubleshooting.

- `-h` or `--help`: Displays help information about all available arguments.

## Features

- Automatically selects an available port if none is specified.
- Logs the streaming URL(s) for easy access.
- Handles decoding of newly added scrambling of playlist file urls by Stripchat.
- Supports both default and dynamic (via `http://<host>:<port>/<username>`) username requests.
- Allows setting a default stream at startup for convenience.
- Supports multiple concurrent connections for simultaneous users.
- Mimics browser-like behavior with appropriate headers.
- Optionally uses a proxy server for all requests.

## License

For educational purposes only. Do not use for any commercial activities.
