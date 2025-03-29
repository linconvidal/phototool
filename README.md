# PhotoTool

A command-line tool for efficient photography workflow management. Organizes photos by capture date and synchronizes between drives.

## Features

- **Import from SD**: Copy photos from SD cards to organized YYYY.MM folders
- **Auto-organization**: Uses EXIF data to sort photos by capture date
- **Sidecar handling**: Automatically copies associated sidecar files (.xmp, .raf, etc.)
- **Drive sync**: Rsync integration for reliable syncing between drives
- **Parallel processing**: Multi-threaded file operations for faster imports
- **Duplicate detection**: Skips identical files to prevent duplicates
- **Edited versions support**: Recognizes and copies edited versions of photos with naming patterns like basename-1.jpg, basename-HDR.heic, etc.

## Installation

### Using uv (recommended)

```bash
# Install uv if you don't have it
curl -sSf https://raw.githubusercontent.com/astral-sh/uv/main/install.sh | bash

# Create virtual environment
uv venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate  # Windows

# Install dependencies
uv pip install -r requirements.txt
```

### Using pip

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

## Requirements

- Python 3.6+
- ExifTool installed on your system
- Rsync (for sync functionality)

## Usage

Run the script directly:

```bash
python myphotoscript.py
```

### Import from SD

Copies and organizes photos from SD card to your SSD:

- Automatically sorts into YYYY.MM folders based on EXIF data
- Detects and copies sidecar files
- Checks for duplicates to avoid copying the same file twice
- Handles edited versions with different naming patterns (e.g., DSF7942-1.JPG, DSF7942-HDR.HEIC)

### Sync drives (rsync)

Synchronizes your photo library between drives:

- Fast, efficient rsync-based sync
- Option to exclude MOV files
- Optional deletion of files no longer in source

## Testing

The project includes a test suite built with pytest to ensure reliability.

### Running Tests

Install pytest first:

```bash
# Using uv
uv pip install pytest

# Using pip
pip install pytest
```

Run the tests:

```bash
python -m pytest test_myphotoscript.py -v
```

### Test Coverage

The tests cover key functionalities:

- **Edited versions handling**: Verifies that variations of a file (DSF7942.RAF → DSF7942-1.JPG, DSF7942-HDR.HEIC) are properly detected and copied
- **Duplicate detection**: Ensures identical files aren't copied twice
- **Error handling**: Validates proper error responses during file operations

### Adding Tests

When adding new features, please extend the test suite with appropriate test cases.

## License

MIT
