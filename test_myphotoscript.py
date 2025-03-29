import os
import shutil
import tempfile
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock

# Import the module to test
import myphotoscript


# Create fixtures for temporary directories
@pytest.fixture
def temp_directories():
    """Create temporary source and destination directories for testing."""
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dest_dir:
        yield Path(src_dir), Path(dest_dir)


# Mock the console to prevent output during tests
@pytest.fixture
def mock_console():
    with patch("myphotoscript.console") as mock_console:
        yield mock_console


# Mock files_are_identical to always return False (files not identical)
@pytest.fixture
def mock_files_not_identical():
    with patch("myphotoscript.files_are_identical", return_value=False) as mock_func:
        yield mock_func


def create_test_files(src_dir):
    """Create test files with various naming patterns in the source directory."""
    # Create main RAW file
    raw_file = src_dir / "DSF7942.RAF"
    raw_file.touch()

    # Create standard sidecar
    xmp_file = src_dir / "DSF7942.XMP"
    xmp_file.touch()

    # Create edited versions with different naming patterns
    edited_files = [
        "DSF7942.JPG",  # Original JPG from camera
        "DSF7942-1.JPG",  # Dash + number
        "DSF7942-HDR.HEIC",  # Dash + text
        "DSF7942_edit.JPG",  # Underscore + text
        "DSF7942 edited.JPG",  # Space + text
        "DSF7942(1).PNG",  # Parenthesis
    ]

    created_files = [raw_file, xmp_file]

    for filename in edited_files:
        file_path = src_dir / filename
        file_path.touch()
        created_files.append(file_path)

    return created_files


def test_move_file_and_sidecars_edited_versions(
    temp_directories, mock_console, mock_files_not_identical
):
    """Test that edited versions are properly recognized and copied."""
    src_dir, dest_dir = temp_directories

    # Create test files
    test_files = create_test_files(src_dir)
    raw_file = src_dir / "DSF7942.RAF"

    # Make sure we have appropriate extensions defined for testing
    myphotoscript.PHOTO_EXTENSIONS = [".jpg", ".jpeg", ".heic", ".png", ".raf"]
    myphotoscript.SIDECAR_EXTENSIONS = [".xmp"]

    # Call function being tested
    success, copied_sidecars, already_exists = myphotoscript.move_file_and_sidecars(
        raw_file, dest_dir, verbose=True
    )

    # Verify function result
    assert success is True

    # Verify main file was copied
    assert (dest_dir / raw_file.name).exists()

    # Test sidecar was copied
    assert (dest_dir / "DSF7942.XMP").exists()

    # Test edited versions were copied
    edited_versions = [
        "DSF7942.JPG",
        "DSF7942-1.JPG",
        "DSF7942-HDR.HEIC",
        "DSF7942_edit.JPG",
        "DSF7942 edited.JPG",
        "DSF7942(1).PNG",
    ]

    for edited_version in edited_versions:
        assert (
            dest_dir / edited_version
        ).exists(), f"Edited version {edited_version} was not copied"

    # Verify total copied count
    assert copied_sidecars == 7  # Expected count: 6 edited versions + 1 XMP sidecar
    assert already_exists is False


def test_move_file_and_sidecars_skips_duplicates(temp_directories, mock_console):
    """Test that duplicate files are skipped when they already exist in destination."""
    src_dir, dest_dir = temp_directories

    # Create test files
    test_files = create_test_files(src_dir)
    raw_file = src_dir / "DSF7942.RAF"

    # Make sure we have appropriate extensions defined for testing
    myphotoscript.PHOTO_EXTENSIONS = [".jpg", ".jpeg", ".heic", ".png", ".raf"]
    myphotoscript.SIDECAR_EXTENSIONS = [".xmp"]

    # Create identical files in destination (to be skipped)
    for src_file in test_files:
        dest_file = dest_dir / src_file.name
        dest_file.touch()  # Create empty file

    # Mock files_are_identical to return True (files are identical)
    with patch("myphotoscript.files_are_identical", return_value=True):
        # Call function being tested
        success, copied_sidecars, already_exists = myphotoscript.move_file_and_sidecars(
            raw_file, dest_dir, verbose=True
        )

    # Verify function result - all files should be skipped
    assert success is True
    assert copied_sidecars == 0  # No files copied
    assert already_exists is True  # Main file already exists


def test_move_file_and_sidecars_handles_errors(temp_directories, mock_console):
    """Test error handling during file copying."""
    src_dir, dest_dir = temp_directories

    # Create test files
    test_files = create_test_files(src_dir)
    raw_file = src_dir / "DSF7942.RAF"

    # Make sure we have appropriate extensions defined for testing
    myphotoscript.PHOTO_EXTENSIONS = [".jpg", ".jpeg", ".heic", ".png", ".raf"]
    myphotoscript.SIDECAR_EXTENSIONS = [".xmp"]

    # Mock shutil.copy2 to raise an OSError
    with patch("shutil.copy2", side_effect=OSError("Test error")):
        # Call function being tested
        success, copied_sidecars, already_exists = myphotoscript.move_file_and_sidecars(
            raw_file, dest_dir, verbose=True
        )

    # Verify function result
    assert success is False
    assert copied_sidecars == 0
    assert already_exists is False

    # Verify proper error message was displayed
    mock_console.print.assert_any_call(
        "  [bold red]ERROR[/] Failed to copy DSF7942.RAF: Test error"
    )


if __name__ == "__main__":
    pytest.main(["-v", "test_myphotoscript.py"])
