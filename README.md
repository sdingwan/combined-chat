# Combined Twitch & Kick Chat

A real-time chat aggregator that combines Twitch and Kick chat streams into a single, unified interface. Built with FastAPI and WebSockets for real-time communication.

## Features

- **Multi-Platform Support**: Connect to both Twitch and Kick chat simultaneously
- **Real-Time Updates**: Live chat messages with WebSocket connections
- **Modern UI**: Clean, responsive interface with dark theme
- **Badge Support**: Display user badges and platform indicators
- **Account Login**: OAuth sign-in for Twitch and Kick with persistent tokens
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
- PostgreSQL 13+ (running instance reachable via `DATABASE_URL`)

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

4. **Configure environment**
   Create a `.env` file (or export the variables) with at least:
   ```env
   DATABASE_URL=sqlite+aiosqlite:///data/app.db
   SESSION_SECRET=change_me
   TWITCH_CLIENT_ID=your_twitch_client_id
   TWITCH_CLIENT_SECRET=your_twitch_client_secret
   TWITCH_REDIRECT_URI=http://localhost:8000/auth/twitch/callback
   KICK_CLIENT_ID=your_kick_client_id
   KICK_CLIENT_SECRET=your_kick_client_secret
   KICK_REDIRECT_URI=http://localhost:8000/auth/kick/callback
   KICK_SCOPES=user:read channel:read chat:read chat:write
   ```
   Adjust values to match your deployed URLs. The redirect URIs must match the ones registered with each provider. The default SQLite database file will be created under `data/app.db` relative to the project root.

5. **Run the application**
   ```bash
   uvicorn app.main:app --reload
   ```

6. **Open your browser**
   Navigate to `http://localhost:8000`

## Usage

1. **Start the server** using the command above
2. **Open the web interface** in your browser
3. **Authenticate** with the platforms you want to post to by clicking **Login with Twitch** and/or **Login with Kick**. Successful logins store tokens in Postgres.
4. **Enter streamer names**:
   - Twitch: Enter the streamer's username (e.g., `summit1g`)
   - Kick: Enter the streamer's username (e.g., `xqc`)
   - You can connect to one or both platforms
5. **Click Connect** to start receiving chat messages
6. **Send messages** from the combined input using the linked platform selector. Messages are posted via your authenticated account.

## API Endpoints

- `GET /` - Serves the main web interface
- `WebSocket /ws` - Real-time chat message stream
- `GET /auth/status` - Returns the currently authenticated user and linked accounts
- `GET /auth/{platform}/login` - Starts an OAuth flow for `twitch` or `kick`
- `GET /auth/{platform}/callback` - OAuth redirect handler storing tokens
- `POST /auth/logout` - Ends the current session
- `POST /chat/send` - Sends a chat message using the linked account

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
│   ├── config.py               # Environment configuration
│   ├── db.py                   # Async SQLAlchemy engine/session
│   ├── models.py               # ORM models for users, sessions, tokens
│   ├── main.py                 # FastAPI application and WebSocket handling
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── session.py          # Session cookie helpers
│   │   └── state.py            # OAuth state persistence
│   ├── routes/
│   │   └── chat.py             # REST endpoint for sending chat messages
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
- **SQLAlchemy**: Async ORM for managing PostgreSQL persistence
- **asyncpg**: PostgreSQL driver used by SQLAlchemy's async engine

## Development

### Running in Development Mode

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Code Structure

- `app/main.py`: Main FastAPI application with WebSocket endpoint
- `app/config.py`: Centralised environment configuration
- `app/db.py`: Async SQLAlchemy engine and session factory
- `app/models.py`: ORM models for users, sessions, and OAuth accounts
- `app/auth/`: Session helpers and OAuth state storage
- `app/routes/chat.py`: REST endpoint for sending chat messages
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
4. **Kick OAuth issues**: Kick's public OAuth and chat APIs are still evolving. Confirm your client credentials and endpoints match the latest Kick developer documentation.

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
