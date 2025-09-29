# Combined Twitch & Kick Chat

A real-time chat aggregator that combines Twitch and Kick chat streams into a single, unified interface. Built with FastAPI and WebSockets for real-time communication.

## Features

- **Multi-Platform Support**: Connect to both Twitch and Kick chat simultaneously
- **Real-Time Updates**: Live chat messages with WebSocket connections
- **Modern UI**: Clean, responsive interface with dark theme
- **Badge Support**: Display user badges and platform indicators
- **Easy Setup**: Simple configuration with streamer names

## Screenshots

The application provides a unified chat interface where you can:
- Enter Twitch and/or Kick streamer names
- View real-time chat messages from both platforms
- See platform indicators and user badges
- Enjoy a modern, responsive design

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/combined-chat.git
   cd combined-chat
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   uvicorn app.main:app --reload
   ```

5. **Open your browser**
   Navigate to `http://localhost:8000`

## Usage

1. **Start the server** using the command above
2. **Open the web interface** in your browser
3. **Enter streamer names**:
   - Twitch: Enter the streamer's username (e.g., `summit1g`)
   - Kick: Enter the streamer's username (e.g., `xqc`)
   - You can connect to one or both platforms
4. **Click Connect** to start receiving chat messages
5. **View real-time messages** from both platforms in a unified feed

## API Endpoints

- `GET /` - Serves the main web interface
- `WebSocket /ws` - Real-time chat message stream

### WebSocket Protocol

Send a JSON message to subscribe to chat streams:
```json
{
  "action": "subscribe",
  "twitch": "streamer_name",
  "kick": "streamer_name"
}
```

## Project Structure

```
combined-chat/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application and WebSocket handling
│   └── chat_sources/
│       ├── __init__.py
│       ├── twitch.py           # Twitch chat client
│       └── kick.py             # Kick chat client
├── static/
│   └── index.html              # Frontend interface
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

## Dependencies

- **FastAPI**: Modern web framework for building APIs
- **uvicorn**: ASGI server for running the application
- **httpx**: HTTP client for API requests
- **websockets**: WebSocket client for real-time communication

## Development

### Running in Development Mode

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Code Structure

- `app/main.py`: Main FastAPI application with WebSocket endpoint
- `app/chat_sources/twitch.py`: Twitch IRC chat client implementation
- `app/chat_sources/kick.py`: Kick WebSocket chat client implementation
- `static/index.html`: Frontend with vanilla JavaScript

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is open source and available under the [MIT License](LICENSE).

## Troubleshooting

### Common Issues

1. **Port already in use**: Change the port with `--port 8001`
2. **WebSocket connection failed**: Ensure the server is running and accessible
3. **Chat not loading**: Check that streamer names are correct and channels are live

### Getting Help

If you encounter any issues:
1. Check the console for error messages
2. Ensure all dependencies are installed
3. Verify streamer names are correct
4. Check that the channels are currently live

## Future Enhancements

- [ ] Add more streaming platforms (YouTube, Facebook Gaming)
- [ ] Chat filtering and moderation tools
- [ ] User authentication and preferences
- [ ] Mobile app support
- [ ] Chat history and search
- [ ] Custom themes and layouts
