#!/usr/bin/env python3

import os
import shutil
import subprocess
from pathlib import Path
import datetime
import hashlib
import concurrent.futures
import threading

import inquirer
import exiftool  # uses exiftool under the hood
from rich.console import Console
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    SpinnerColumn,
    TimeElapsedColumn,
    TaskID,
)
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Initialize rich console
console = Console()

# Define global constants
SIDECAR_EXTENSIONS = [".xmp", ".raf", ".fp2", ".fp3", ".photo-edit", ".dng"]

# -----------------------------------------
# 1. EXIF Functions
# -----------------------------------------


def get_exif_date(image_path):
    """
    Attempt to retrieve 'DateTimeOriginal' (or a fallback) from the file's metadata.
    Returns (year, month) as strings, or None if not found.
    """
    metadata_keys = [
        "EXIF:DateTimeOriginal",
        "EXIF:CreateDate",
        "QuickTime:CreateDate",  # in case of MOV or MP4
    ]

    try:
        with exiftool.ExifTool() as et:
            metadata = et.execute_json("-j", "-n", str(image_path))[0]
    except Exception as e:
        console.print(
            f"[bold red]ERROR[/] Failed to read metadata from {image_path.name}: {str(e)}"
        )
        # Fallback: if we can't read metadata, use current date
        now = datetime.datetime.now()
        return now.strftime("%Y"), now.strftime("%m")

    # Look for a known date key
    date_str = None
    for key in metadata_keys:
        if key in metadata:
            date_str = metadata[key]
            break

    if not date_str:
        # Fallback: if we can't find any date in metadata, use current date
        now = datetime.datetime.now()
        return now.strftime("%Y"), now.strftime("%m")

    # date_str might be in format '2025:03:22 10:11:12' or '2025-03-22T10:11:12Z'
    # We just need year, month. We'll do a parse approach:
    date_str_clean = date_str.replace("-", ":").replace("T", " ")
    # We expect something like 'YYYY:MM:DD HH:MM:SS' now
    parts = date_str_clean.split()
    date_part = parts[0].split(":")  # [YYYY, MM, DD]

    if len(date_part) >= 2:
        year = date_part[0]
        month = date_part[1]
        return year, month
    else:
        # Fallback if unexpected format
        now = datetime.datetime.now()
        return now.strftime("%Y"), now.strftime("%m")


# -----------------------------------------
# 2. File Organization Functions
# -----------------------------------------


def calculate_file_checksum(file_path, algorithm="md5", buffer_size=65536):
    """
    Calculate a file's checksum using the specified algorithm.
    """
    if algorithm == "md5":
        hash_obj = hashlib.md5()
    elif algorithm == "sha256":
        hash_obj = hashlib.sha256()
    else:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    try:
        with open(file_path, "rb") as f:
            while True:
                data = f.read(buffer_size)
                if not data:
                    break
                hash_obj.update(data)
        return hash_obj.hexdigest()
    except Exception as e:
        console.print(
            f"[bold red]ERROR[/] Failed to calculate checksum for {file_path}: {str(e)}"
        )
        return None


def files_are_identical(src_path, dest_path):
    """
    Compare two files using checksum to determine if they're identical.
    """
    if not dest_path.exists():
        return False

    # First quick check: compare file sizes
    if src_path.stat().st_size != dest_path.stat().st_size:
        return False

    # Then do the more expensive checksum comparison
    src_checksum = calculate_file_checksum(src_path)
    dest_checksum = calculate_file_checksum(dest_path)

    if src_checksum and dest_checksum:
        return src_checksum == dest_checksum
    return False


def build_destination_folder(root_folder, year, month):
    """
    Create and return a path like /root_folder/2025.03
    Returns Path object or raises an exception if folder cannot be created
    """
    folder_name = f"{year}.{month}"
    dest_path = Path(root_folder) / folder_name

    try:
        dest_path.mkdir(parents=True, exist_ok=True)
        return dest_path
    except OSError as e:
        if e.errno == 6:  # Device not configured
            raise OSError(
                f"Destination drive disconnected while creating folder {folder_name}"
            )
        else:
            raise OSError(f"Failed to create folder {dest_path}: {str(e)}")
    except Exception as e:
        raise Exception(f"Failed to create folder {dest_path}: {str(e)}")


def move_file_and_sidecars(
    src_file, dest_folder, sidecar_exts=None, verbose=False, progress=None, task_id=None
):
    """
    Copy the main file plus any sidecar files that share the same base name.
    Returns tuple: (success, copied_sidecars_count, already_exists)
    """
    if sidecar_exts is None:
        sidecar_exts = SIDECAR_EXTENSIONS

    base_name = src_file.stem  # e.g. "DSCF001" from "DSCF001.RAF"
    src_parent = src_file.parent

    # Check if file already exists with same content
    dest_file_path = dest_folder / src_file.name
    if dest_file_path.exists():
        if files_are_identical(src_file, dest_file_path):
            if verbose:
                console.print(
                    f"  [yellow]SKIP[/] {src_file.name} (identical file already exists)"
                )
            return True, 0, True
        else:
            console.print(
                f"  [bold yellow]WARNING[/] File with same name exists but content differs: {src_file.name}"
            )

    # Copy the main file
    try:
        shutil.copy2(str(src_file), str(dest_folder / src_file.name))
        if verbose:
            console.print(f"  [green]Copied[/] main file: {src_file.name}")
    except OSError as e:
        if e.errno == 6:  # Device not configured
            console.print(
                f"  [bold red]ERROR[/] Device disconnected while copying {src_file.name}"
            )
        else:
            console.print(
                f"  [bold red]ERROR[/] Failed to copy {src_file.name}: {str(e)}"
            )
        return False, 0, False

    # Count copied sidecars for reporting
    copied_sidecars = 0
    # Track which sidecars have been copied to avoid duplicates
    copied_sidecar_paths = set()

    # Copy sidecar files - search in case-insensitive way
    all_files_in_parent = list(src_parent.glob("*"))
    base_name_lower = base_name.lower()

    for file_path in all_files_in_parent:
        if not file_path.is_file():
            continue

        # Check if this is a sidecar for our main file
        if file_path.stem.lower() == base_name_lower and file_path != src_file:
            extension = file_path.suffix.lower()
            if extension in [ext.lower() for ext in sidecar_exts]:
                # Skip if we've already copied this file (normalized path)
                norm_path = str(file_path).lower()
                if norm_path in copied_sidecar_paths:
                    continue

                # Check if sidecar already exists with same content
                sidecar_dest_path = dest_folder / file_path.name
                if sidecar_dest_path.exists() and files_are_identical(
                    file_path, sidecar_dest_path
                ):
                    if verbose:
                        console.print(
                            f"  [yellow]SKIP[/] Sidecar {file_path.name} (identical file already exists)"
                        )
                    continue

                try:
                    shutil.copy2(str(file_path), str(dest_folder / file_path.name))
                    copied_sidecars += 1
                    copied_sidecar_paths.add(norm_path)
                    if verbose:
                        console.print(f"  [green]Copied[/] sidecar: {file_path.name}")
                except OSError as e:
                    if e.errno == 6:  # Device not configured
                        console.print(
                            f"  [bold red]ERROR[/] Device disconnected while copying sidecar {file_path.name}"
                        )
                        return False, copied_sidecars, False
                    else:
                        console.print(
                            f"  [bold red]ERROR[/] Failed to copy sidecar {file_path.name}: {str(e)}"
                        )

    # Also try direct matching with common patterns
    for ext in sidecar_exts:
        # Try both lower and upper case versions of the extension
        for test_ext in [ext.lower(), ext.upper()]:
            sidecar_path = src_parent / f"{base_name}{test_ext}"
            # Skip if we've already copied this file (normalized path)
            norm_path = str(sidecar_path).lower()
            if norm_path in copied_sidecar_paths:
                continue

            if sidecar_path.exists() and sidecar_path != src_file:
                # Check if sidecar already exists with same content
                sidecar_dest_path = dest_folder / sidecar_path.name
                if sidecar_dest_path.exists() and files_are_identical(
                    sidecar_path, sidecar_dest_path
                ):
                    if verbose:
                        console.print(
                            f"  [yellow]SKIP[/] Sidecar {sidecar_path.name} (identical file already exists)"
                        )
                    continue

                try:
                    shutil.copy2(
                        str(sidecar_path), str(dest_folder / sidecar_path.name)
                    )
                    copied_sidecars += 1
                    copied_sidecar_paths.add(norm_path)
                    if verbose:
                        console.print(
                            f"  [green]Copied[/] sidecar: {sidecar_path.name}"
                        )
                except OSError as e:
                    if e.errno == 6:  # Device not configured
                        console.print(
                            f"  [bold red]ERROR[/] Device disconnected while copying sidecar {sidecar_path.name}"
                        )
                        return False, copied_sidecars, False
                    else:
                        console.print(
                            f"  [bold red]ERROR[/] Failed to copy sidecar {sidecar_path.name}: {str(e)}"
                        )

    if progress and task_id:
        progress.update(task_id, advance=1)

    return True, copied_sidecars, False


# -----------------------------------------
# 3. "Import from SD" Workflow
# -----------------------------------------


def import_from_sd(sd_folder, ssd_root, skip_mov=False, verbose=False, max_workers=4):
    """
    Organize photos by YYYY.MM in ssd_root from sd_folder.
    Recursively scans all subdirectories.
    Optionally skip .mov files.
    Uses parallel processing for faster copying.
    """
    sd_folder = Path(sd_folder)
    ssd_root = Path(ssd_root)

    # Check if source and destination are available
    if not sd_folder.is_dir():
        console.print(
            f"\n[bold red]ERROR[/] SD folder '{sd_folder}' does not exist or is not accessible.\n"
        )
        return

    if not ssd_root.exists():
        try:
            ssd_root.mkdir(parents=True, exist_ok=True)
            console.print(f"\n[blue]INFO[/] Created destination folder: {ssd_root}")
        except Exception as e:
            console.print(
                f"\n[bold red]ERROR[/] Cannot create destination folder '{ssd_root}': {str(e)}\n"
            )
            return

    if not ssd_root.is_dir():
        console.print(
            f"\n[bold red]ERROR[/] Destination '{ssd_root}' exists but is not a directory.\n"
        )
        return

    # Use the global sidecar extensions list
    sidecar_exts = SIDECAR_EXTENSIONS

    # Use try-except for the initial file scan
    try:
        console.print("\n")  # Add spacing before scan message
        console.print(
            Panel(
                "[blue]Scanning SD card for files...[/]", expand=False, padding=(1, 2)
            )
        )
        # Use rglob to recursively search all subdirectories
        all_files = list(sd_folder.rglob("*"))
        console.print(f"[green]Found {len(all_files)} total items[/]")
        console.print("\n")  # Add spacing after scan results
    except OSError as e:
        console.print(f"\n[bold red]ERROR[/] Failed to scan SD card: {str(e)}")
        if e.errno == 6:  # Device not configured
            console.print("[bold red]ERROR[/] The SD card appears to be disconnected.")
        return

    # Filter out sidecar files from main processing
    files = [
        f for f in all_files if f.is_file() and f.suffix.lower() not in sidecar_exts
    ]

    # Count metrics
    total_files = len(files)
    # Using thread-safe counters
    lock = threading.Lock()
    metrics = {
        "processed_files": 0,
        "copied_files": 0,
        "copied_sidecars": 0,
        "skipped_files": 0,
        "skipped_existing": 0,
        "failed_files": 0,
    }

    # Lists to track files that were skipped or failed
    skipped_files_list = []
    skipped_existing_list = []
    failed_files_list = []

    console.print(
        Panel(
            f"[blue]Starting parallel import of [bold]{total_files}[/] main files from [bold]{sd_folder}[/][/]",
            expand=False,
            padding=(1, 2),
        )
    )
    console.print("\n")  # Add spacing after import start panel

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("[cyan]{task.completed}/{task.total}[/]"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        task_id = progress.add_task("[green]Importing files...", total=total_files)

        def process_file(file_item):
            nonlocal metrics, skipped_files_list, skipped_existing_list, failed_files_list

            # Check if we should skip this file
            if not file_item.is_file():
                if verbose:
                    console.print(f"[yellow]SKIP[/] {file_item.name} (not a file)")
                with lock:
                    metrics["skipped_files"] += 1
                    skipped_files_list.append((str(file_item), "not a file"))
                progress.update(task_id, advance=1)
                return

            # Optionally skip .mov
            if skip_mov and file_item.suffix.lower() == ".mov":
                if verbose:
                    console.print(f"[yellow]SKIP[/] {file_item.name} (.mov file)")
                with lock:
                    metrics["skipped_files"] += 1
                    skipped_files_list.append((str(file_item), ".mov file"))
                progress.update(task_id, advance=1)
                return

            if verbose:
                with lock:
                    console.print(
                        f"[blue]Processing[/] {file_item.name} ({metrics['processed_files'] + 1}/{total_files})"
                    )

            try:
                # Get date from EXIF
                year, month = get_exif_date(file_item)

                # Build destination folder
                dest_folder = build_destination_folder(ssd_root, year, month)

                if verbose:
                    console.print(f"  -> Copying to {dest_folder}")

                # Copy file + sidecars and count the sidecars
                success, sidecar_count, already_exists = move_file_and_sidecars(
                    file_item,
                    dest_folder,
                    sidecar_exts,
                    verbose=verbose,
                    progress=progress,
                    task_id=task_id,
                )

                with lock:
                    metrics["processed_files"] += 1
                    if already_exists:
                        metrics["skipped_existing"] += 1
                        skipped_existing_list.append(str(file_item))
                        if not verbose:
                            progress.update(task_id, advance=1)
                    elif success:
                        metrics["copied_files"] += 1
                        metrics["copied_sidecars"] += sidecar_count
                        if not verbose:
                            progress.update(task_id, advance=1)
                    else:
                        metrics["failed_files"] += 1
                        failed_files_list.append((str(file_item), "copy failed"))
                        console.print(
                            f"[bold yellow]WARNING[/] Failed to copy {file_item.name} - device may be disconnected"
                        )
                        if not verbose:
                            progress.update(task_id, advance=1)
            except Exception as e:
                with lock:
                    metrics["failed_files"] += 1
                    failed_files_list.append((str(file_item), str(e)))
                    console.print(
                        f"[bold red]ERROR[/] Failed to process {file_item.name}: {str(e)}"
                    )
                progress.update(task_id, advance=1)

        # Use ThreadPoolExecutor for parallel copying
        # ThreadPoolExecutor is better than ProcessPoolExecutor for I/O bound operations
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all files to the executor
            futures = [executor.submit(process_file, file_item) for file_item in files]

            # Wait for all futures to complete
            concurrent.futures.wait(futures)

            # Check if any exception was raised
            for future in futures:
                if future.exception() is not None:
                    console.print(
                        f"[bold red]ERROR[/] Task failed: {future.exception()}"
                    )

    # Print summary as a table
    table = Table(title="Import Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green")

    table.add_row("Total main files processed", str(metrics["processed_files"]))
    table.add_row("Files copied", str(metrics["copied_files"]))
    table.add_row("Files skipped (already exist)", str(metrics["skipped_existing"]))
    table.add_row("Sidecar files copied", str(metrics["copied_sidecars"]))
    table.add_row("Items skipped (other reasons)", str(metrics["skipped_files"]))
    table.add_row("Items failed", str(metrics["failed_files"]))
    table.add_row("Destination root", str(ssd_root))

    console.print(Panel.fit(table, title="[bold green]IMPORT COMPLETE[/]"))

    # Always log files that were skipped or failed, regardless of verbose setting
    if skipped_files_list:
        skip_table = Table(title=f"Skipped Files ({len(skipped_files_list)})")
        skip_table.add_column("File", style="yellow")
        skip_table.add_column("Reason", style="cyan")

        # Limit the output to avoid overwhelming the console
        max_display = 50
        for file_path, reason in skipped_files_list[:max_display]:
            skip_table.add_row(file_path, reason)

        if len(skipped_files_list) > max_display:
            skip_table.add_row(
                f"... and {len(skipped_files_list) - max_display} more", ""
            )

        console.print(skip_table)

    if skipped_existing_list:
        existing_table = Table(
            title=f"Files Already Existing ({len(skipped_existing_list)})"
        )
        existing_table.add_column("File", style="yellow")

        # Limit the output to avoid overwhelming the console
        max_display = 50
        for file_path in skipped_existing_list[:max_display]:
            existing_table.add_row(file_path)

        if len(skipped_existing_list) > max_display:
            existing_table.add_row(
                f"... and {len(skipped_existing_list) - max_display} more"
            )

        console.print(existing_table)

    if failed_files_list:
        failed_table = Table(title=f"Failed Files ({len(failed_files_list)})")
        failed_table.add_column("File", style="red")
        failed_table.add_column("Error", style="red")

        # Limit the output to avoid overwhelming the console
        max_display = 50
        for file_path, error in failed_files_list[:max_display]:
            failed_table.add_row(file_path, error)

        if len(failed_files_list) > max_display:
            failed_table.add_row(
                f"... and {len(failed_files_list) - max_display} more", ""
            )

        console.print(failed_table)


# -----------------------------------------
# 4. "Rsync" Workflow
# -----------------------------------------


def rsync_folders(source, destination, exclude_mov=False, do_delete=False):
    """
    Construct and run an rsync command with the chosen options.
    """
    rsync_cmd = [
        "rsync",
        "-avh",  # archive, verbose, human-readable
        f"{source}/",  # ensure trailing slash for source
        f"{destination}/",
    ]

    if exclude_mov:
        rsync_cmd.insert(2, "--exclude=*.mov")

    if do_delete:
        rsync_cmd.insert(2, "--delete")

    console.print(Panel(f"[blue]Running:[/] {' '.join(rsync_cmd)}", expand=False))

    with console.status("[bold green]Running rsync...", spinner="dots"):
        try:
            result = subprocess.run(
                rsync_cmd, check=True, capture_output=True, text=True
            )
            console.print("[bold green]Rsync completed successfully![/]")

            # Parse rsync output for summary
            output_lines = result.stdout.strip().split("\n")
            if output_lines:
                summary_line = (
                    output_lines[-1] if output_lines[-1].startswith("sent") else None
                )
                if summary_line:
                    console.print(f"[green]{summary_line}[/]")

        except subprocess.CalledProcessError as e:
            console.print(f"[bold red]ERROR[/] rsync failed with code {e.returncode}.")
            if e.stderr:
                console.print(f"[red]{e.stderr}[/]")


# -----------------------------------------
# 5. Main Interactive CLI with python-inquirer
# -----------------------------------------


def main_menu():
    """
    Show the main menu (Import from SD, Sync, Quit).
    """
    console.print("\n")  # Add extra blank line for spacing
    console.print(
        Panel.fit(
            "[cyan bold]Photo Management Tool[/]", subtitle="v1.0", padding=(1, 2)
        )
    )
    console.print("\n")  # Add extra blank line for spacing

    questions = [
        inquirer.List(
            "main_choice",
            message="What do you want to do?",
            choices=["Import from SD card", "Sync drives (rsync)", "Quit"],
        )
    ]
    answers = inquirer.prompt(questions)
    return answers["main_choice"]


def import_menu():
    """
    Ask user for SD card folder, SSD root, and whether to skip .mov
    """
    console.print("\n")  # Add spacing
    questions = [
        inquirer.Text(
            "sd_folder",
            message="Path to SD card/folder?",
            default="/Volumes/SDCARD/DCIM",
        ),
        inquirer.Text(
            "ssd_root", message="Path to SSD 'imgs' root?", default="/Volumes/ssd/imgs"
        ),
        inquirer.Confirm("skip_mov", message="Skip .mov files?", default=True),
        inquirer.Confirm("verbose", message="Show detailed output?", default=False),
        inquirer.Text(
            "max_workers", message="How many parallel workers to use?", default="8"
        ),
    ]
    return inquirer.prompt(questions)


def rsync_menu():
    """
    Ask user for source, destination, exclude .mov, and use --delete
    """
    console.print("\n")  # Add spacing
    questions = [
        inquirer.Text("source", message="Source folder?", default="/Volumes/ssd/imgs"),
        inquirer.Text(
            "destination", message="Destination folder?", default="/Volumes/hdd/imgs"
        ),
        inquirer.Confirm(
            "exclude_mov", message="Exclude .mov files from syncing?", default=False
        ),
        inquirer.Confirm(
            "do_delete",
            message="Use --delete on destination files not in source?",
            default=False,
        ),
    ]
    return inquirer.prompt(questions)


def main():
    console.print("\n")  # Start with blank line
    console.print(
        Panel.fit(
            "[bold cyan]Photography Management Tool[/]",
            subtitle="Organize and sync your photos with ease",
            padding=(1, 3),  # Add more padding
            width=80,  # Specify width to prevent subtitle truncation
        )
    )

    while True:
        try:
            choice = main_menu()

            if choice == "Import from SD card":
                ans = import_menu()
                try:
                    import_from_sd(
                        sd_folder=ans["sd_folder"],
                        ssd_root=ans["ssd_root"],
                        skip_mov=ans["skip_mov"],
                        verbose=ans["verbose"],
                        max_workers=int(ans["max_workers"]),
                    )
                except Exception as e:
                    console.print(
                        f"\n[bold red]ERROR[/] Import process failed: {str(e)}"
                    )
                    console.print(
                        "[yellow]Please check if all drives are properly connected and try again.[/]"
                    )

            elif choice == "Sync drives (rsync)":
                ans = rsync_menu()
                try:
                    rsync_folders(
                        source=ans["source"],
                        destination=ans["destination"],
                        exclude_mov=ans["exclude_mov"],
                        do_delete=ans["do_delete"],
                    )
                except Exception as e:
                    console.print(f"\n[bold red]ERROR[/] Sync process failed: {str(e)}")
                    console.print(
                        "[yellow]Please check if all drives are properly connected and try again.[/]"
                    )

            elif choice == "Quit":
                console.print(
                    "\n[blue]INFO[/] Exiting the tool.\n"
                )  # Add newline after exit message
                break

        except KeyboardInterrupt:
            console.print("\n[blue]INFO[/] Process interrupted by user. Exiting.\n")
            break
        except Exception as e:
            console.print(
                f"\n[bold red]ERROR[/] An unexpected error occurred: {str(e)}"
            )
            console.print("[yellow]The tool will restart. Press Ctrl+C to exit.\n")
            continue


if __name__ == "__main__":
    main()
