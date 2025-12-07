# TikTok Tracker

A comprehensive Python Flask web application for tracking TikTok creators and automatically downloading their content using gallery-dl.

## Features

### Core Functionality
- **User Management**: Add/remove TikTok usernames for tracking
- **Content Downloading**: Automatic video and image download with real-time progress
- **Profile Tracking**: Monitor follower counts, video counts, and profile updates
- **Content Organization**: Automatic folder organization by username
- **Bulk Operations**: Download all content or create ZIP archives

### User Interface
- **Dark Theme**: Modern, responsive dark UI design
- **Grid/List Views**: Switch between card grid and compact list layouts
- **Real-time Progress**: Live download progress with detailed logs
- **Tag System**: Organize users with custom colored tags
- **Filtering & Search**: Filter users by tags and search functionality
- **Pagination**: Handle large user lists efficiently

### Technical Features
- **Background Processing**: Threaded downloads with progress tracking
- **AJAX Integration**: Real-time updates without page refreshes
- **Error Handling**: Comprehensive error handling and retry logic
- **Rate Limiting**: Built-in delays to prevent API abuse
- **SQLite Database**: Lightweight database for user and tag management

## Prerequisites

Before running the application, ensure you have:

1. **Python 3.8+** installed on your system
2. **gallery-dl** installed and accessible from command line:
   ```bash
   pip install gallery-dl
   ```
3. **Flask** and dependencies (see requirements.txt)

## Installation

1. **Clone or download** this project to your desired directory

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Verify gallery-dl installation**:
   ```bash
   gallery-dl --version
   ```

## Usage

### Starting the Application

1. **Run the Flask application**:
   ```bash
   python app.py
   ```

2. **Open your browser** and navigate to:
   ```
   http://localhost:5000
   ```

### Adding Users

1. Click the **"Add User"** button in the top-left corner
2. Enter the TikTok username (without the @ symbol)
3. Click **"Add User"** to confirm

### Downloading Content

1. **Individual Downloads**: Click the "Download" button on any user card
2. **Monitor Progress**: Real-time progress modal shows download status
3. **ZIP Archives**: Use the context menu to download user content as ZIP

### Managing Tags

1. Click **"Manage Tags"** to create custom tags
2. **Assign tags** to users for organization
3. **Filter users** by tags using the dropdown

### User Profiles

1. **Click on any username** to view their profile page
2. **Browse downloaded content** with filtering and sorting options
3. **View media** with built-in lightbox for images
4. **Download individual files** or entire collections

## File Structure

```
TrackUI 2/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── templates/
│   ├── layout.html       # Base template
│   ├── index.html        # Main page template
│   └── user.html         # User profile template
├── static/
│   ├── style.css         # Main stylesheet
│   └── app.js           # JavaScript functionality
└── data/
    ├── trackui.db        # SQLite database (created automatically)
    └── downloads/        # Downloaded content storage
        └── [username]/   # Individual user folders
```

## Configuration

### Database
- SQLite database is created automatically on first run
- Located at `data/trackui.db`
- Contains tables for users, tags, and user-tag relationships

### Downloads
- All content stored in `data/downloads/[username]/` folders
- Supports videos (.mp4) and images (.jpg, .jpeg, .png, .gif)
- Metadata and info files included for each download

### gallery-dl Integration
- Uses gallery-dl command-line tool for content extraction
- Supports both metadata extraction and content downloading
- Real-time output streaming for progress tracking

## Features in Detail

### User Management
- **Add Users**: Simple modal interface for adding new users
- **Profile Sync**: Automatic profile data updates (followers, videos, etc.)
- **Tracking Toggle**: Enable/disable tracking per user
- **Bulk Sync**: Sync all tracked users with one click

### Download System
- **Background Processing**: Downloads run in separate threads
- **Progress Tracking**: Real-time file count and current file display
- **Error Handling**: Automatic retries with exponential backoff
- **Rate Limiting**: Built-in delays to respect TikTok's limits

### User Interface
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Dark Theme**: Easy on the eyes with consistent styling
- **Smooth Animations**: Polished transitions and hover effects
- **Keyboard Shortcuts**: Ctrl+N to add users, Esc to close modals

### Content Viewing
- **Media Gallery**: Grid view of downloaded content
- **Filtering**: Show only videos or images
- **Sorting**: Sort by date, name, or file size
- **Lightbox**: Full-screen image viewing
- **File Info**: Detailed file information modal

## Troubleshooting

### Common Issues

1. **gallery-dl not found**:
   - Ensure gallery-dl is installed: `pip install gallery-dl`
   - Verify it's in your PATH: `gallery-dl --version`

2. **Permission errors**:
   - Ensure write permissions to the `data/` directory
   - Run with appropriate user permissions

3. **Network issues**:
   - Check internet connectivity
   - TikTok may block requests - use VPN if needed
   - Wait between requests to avoid rate limiting

4. **Database locked**:
   - Close any other instances of the application
   - Check file permissions on `data/trackui.db`

### Performance Tips

- **Large Collections**: Use pagination and filters for better performance
- **Storage Space**: Monitor disk usage as video files can be large
- **Network Usage**: Be mindful of bandwidth when downloading many videos
- **Rate Limiting**: Space out downloads to avoid being blocked

## Development

### Adding Features
- The application is modular with clear separation of concerns
- Database operations are centralized in helper functions
- Frontend uses vanilla JavaScript for maximum compatibility

### Customization
- Modify CSS custom properties in `style.css` for theme changes
- Add new routes in `app.py` for additional functionality
- Extend database schema by modifying `init_database()` function

## Security Notes

- This application is intended for personal use
- Respect TikTok's Terms of Service and copyright laws
- Be mindful of rate limiting to avoid IP blocks
- Only download content you have permission to download

## License

This project is for educational and personal use only. Respect the terms of service of TikTok and gallery-dl.

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Verify gallery-dl is working independently
3. Check browser console for JavaScript errors
4. Review Flask application logs for server errors