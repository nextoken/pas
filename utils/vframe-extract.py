#!/usr/bin/env python3
"""
@pas-executable
Generic video frame extractor tool (motion-based or uniform interval).
"""

import os
import sys
import time
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any

import cv2
import numpy as np
from rich.panel import Panel

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    prompt_yes_no,
    prompt_toolkit_menu,
    format_menu_choices,
    run_command
)

# --- Configuration ---
DEFAULT_DIFF_THRESHOLD = 0.005
DEFAULT_MIN_INTERVAL = 5
DEFAULT_MAX_FRAMES = 1000
DEFAULT_RECURSIVE_VIDEO_FILENAME = "processed.mp4"
DISPLAY_WINDOW_NAME = "Frame extraction"
DIFF_COMPUTE_TARGET_WIDTH = 320
# ---------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]vframe-extract[/bold cyan] is a generic video frame extraction utility.\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Motion-based:[/bold] Saves frames only when significant movement is detected.\n"
        "• [bold]Uniform Interval:[/bold] Saves exactly X frames evenly spaced across duration.\n"
        "• [bold]Recursive Mode:[/bold] Processes multiple videos found in a directory tree.\n"
        "• [bold]Interactive Mode:[/bold] Guided setup if no arguments are provided."
    )
    console.print(Panel(summary, title="Video Frame Extractor", expand=False))

def _hms_to_seconds(value: str) -> float:
    """
    Convert an ffmpeg-style timestamp (HH:MM:SS[.ms]) to seconds.
    """
    try:
        hms, *rest = value.split(".")
        parts = [float(p) for p in hms.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
        millis = float(f"0.{rest[0]}") if rest else 0.0
        return hours * 3600 + minutes * 60 + seconds + millis
    except Exception as exc:  # noqa: BLE001
        raise argparse.ArgumentTypeError(f"Invalid timestamp: {value}") from exc

def _format_seconds(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm."""
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

def _resolve_path(raw_path: str) -> Path:
    """Resolve path, handling absolute, relative, and home expansion."""
    path = Path(os.path.expanduser(raw_path))
    return path.resolve()

def _get_video_info(video_path: Path) -> dict:
    """
    Get basic information about a video file.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {}
    
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps > 0 else 0
        
        return {
            "fps": fps,
            "frame_count": frame_count,
            "duration": duration,
            "width": width,
            "height": height,
        }
    finally:
        cap.release()

def _compute_frame_difference(prev_bgr: np.ndarray, curr_bgr: np.ndarray) -> float:
    """
    Compute normalized mean absolute difference between two BGR frames.
    """
    prev_gray = cv2.cvtColor(prev_bgr, cv2.COLOR_BGR2GRAY)
    curr_gray = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)

    if prev_gray.shape[1] > DIFF_COMPUTE_TARGET_WIDTH:
        scale = DIFF_COMPUTE_TARGET_WIDTH / prev_gray.shape[1]
        new_size = (DIFF_COMPUTE_TARGET_WIDTH, int(prev_gray.shape[0] * scale))
        prev_gray = cv2.resize(prev_gray, new_size, interpolation=cv2.INTER_AREA)
        curr_gray = cv2.resize(curr_gray, new_size, interpolation=cv2.INTER_AREA)

    diff = cv2.absdiff(prev_gray, curr_gray)
    mad = float(np.mean(diff)) / 255.0
    return mad

def extract_motion_frames(
    video_path: Path,
    output_dir: Path,
    diff_threshold: float = DEFAULT_DIFF_THRESHOLD,
    min_interval: int = DEFAULT_MIN_INTERVAL,
    start_ts: float = 0.0,
    end_ts: Optional[float] = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> list[Path]:
    """
    Extract frames based on motion/difference from the last-saved frame.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    saved_paths: list[Path] = []
    last_saved_frame = None
    last_saved_index = -10**9
    diff_values: list[float] = []
    last_processed_ts: float = start_ts

    frame_idx = 0
    console.print(f"[bold blue]Motion extraction:[/bold blue] {video_path} -> {output_dir}")

    try:
        while True:
            success, frame = cap.read()
            if not success:
                break

            curr_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            curr_sec = curr_ms / 1000.0 if curr_ms > 0 else frame_idx / max(cap.get(cv2.CAP_PROP_FPS) or 1, 1)

            if curr_sec < start_ts:
                frame_idx += 1
                continue
            if end_ts is not None and curr_sec > end_ts:
                last_processed_ts = end_ts
                break
            
            last_processed_ts = curr_sec

            save_this = False
            diff_value = None

            if last_saved_frame is None:
                save_this = True
            else:
                if frame_idx - last_saved_index >= min_interval:
                    diff_value = _compute_frame_difference(last_saved_frame, frame)
                    diff_values.append(diff_value)
                    if diff_value >= diff_threshold:
                        save_this = True

            if save_this:
                # Video-stem + timestamp-based name
                video_stem = video_path.stem
                time_name = int(time.time() * 1000)
                img_path = output_dir / f"{video_stem}_{time_name}.jpeg"
                cv2.imwrite(str(img_path), frame)

                saved_paths.append(img_path)
                last_saved_frame = frame
                last_saved_index = frame_idx
                
                # Check if we've reached the maximum number of frames
                if len(saved_paths) >= max_frames:
                    break
            
            # Simple display
            display = frame.copy()
            text1 = f"Saved: {len(saved_paths)} / {max_frames}"
            text2 = f"Time: {_format_seconds(curr_sec)}"
            cv2.putText(display, text1, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.putText(display, text2, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            if diff_value is not None:
                color = (0, 255, 0) if diff_value >= diff_threshold else (0, 165, 255)
                text3 = f"Diff: {diff_value:.4f} (thr: {diff_threshold:.4f})"
                cv2.putText(display, text3, (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            cv2.imshow(DISPLAY_WINDOW_NAME, display)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            
            frame_idx += 1

    finally:
        cap.release()
        cv2.destroyAllWindows()

    return saved_paths

def extract_uniform_frames(
    video_path: Path,
    output_dir: Path,
    count: int,
    start_ts: float = 0.0,
    end_ts: Optional[float] = None,
) -> list[Path]:
    """
    Extract exactly X frames evenly spaced across video duration.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    info = _get_video_info(video_path)
    if not info:
        raise RuntimeError(f"Could not get video info: {video_path}")
    
    duration = info["duration"]
    actual_end_ts = end_ts if end_ts is not None else duration
    time_range = actual_end_ts - start_ts
    
    if count < 1:
        return []
    
    interval = time_range / count if count > 0 else 0
    
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    saved_paths: list[Path] = []
    console.print(f"[bold blue]Uniform extraction ({count} frames):[/bold blue] {video_path} -> {output_dir}")

    try:
        for i in range(count):
            target_time = start_ts + i * interval
            if target_time >= actual_end_ts:
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, target_time * 1000)
            success, frame = cap.read()
            if not success:
                break
            
            video_stem = video_path.stem
            time_name = int(time.time() * 1000)
            img_path = output_dir / f"{video_stem}_{time_name}.jpeg"
            cv2.imwrite(str(img_path), frame)
            saved_paths.append(img_path)
            
            # Simple display
            display = frame.copy()
            text1 = f"Saved: {len(saved_paths)} / {count}"
            text2 = f"Time: {_format_seconds(target_time)}"
            cv2.putText(display, text1, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.putText(display, text2, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.imshow(DISPLAY_WINDOW_NAME, display)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
                
    finally:
        cap.release()
        cv2.destroyAllWindows()
        
    return saved_paths

def _display_extraction_summary(
    video_path: Optional[Path],
    output_dir: Path,
    start_ts: float,
    end_ts: Optional[float],
    mode: str,
    params: Dict[str, Any],
) -> None:
    """Display pre-execution summary."""
    console.print(f"\n[bold cyan]{'=' * 50}[/bold cyan]")
    console.print("[bold cyan]FRAME EXTRACTION SUMMARY[/bold cyan]")
    console.print(f"[bold cyan]{'=' * 50}[/bold cyan]")

    if video_path:
        console.print(f"\n[bold]Input Video:[/bold] {video_path}")
        info = _get_video_info(video_path)
        if info:
            console.print(f"  Resolution: {info['width']}x{info['height']}")
            console.print(f"  Duration: {_format_seconds(info['duration'])}")
    else:
        console.print(f"\n[bold]Recursive Mode:[/bold] {params.get('recursive_source')}")

    console.print(f"\n[bold]Output Directory:[/bold] {output_dir}")
    console.print(f"[bold]Extraction Mode:[/bold] {mode}")
    
    console.print("\n[bold]Parameters:[/bold]")
    console.print(f"  Time range: {_format_seconds(start_ts)} to {end_ts if end_ts is not None else 'end'}")
    for k, v in params.items():
        if k not in ["recursive_source", "video_filename"]:
            console.print(f"  {k}: {v}")

    console.print("\n[bold cyan]" + "=" * 50 + "[/bold cyan]\n")

def interactive_mode():
    """Guided interactive flow."""
    show_summary()
    
    main_menu = [
        {"title": "Extract frames from single video", "value": "single"},
        {"title": "Extract frames recursively from directory", "value": "recursive"},
        {"title": "[Quit]", "value": "quit"}
    ]
    
    choice = prompt_toolkit_menu(format_menu_choices(main_menu, title_field="title", value_field="value"))
    if not choice or choice == "quit":
        return

    params = {}
    video_path = None
    
    if choice == "single":
        v_path = input("Enter video file path: ").strip()
        if not v_path: return
        video_path = _resolve_path(v_path)
        if not video_path.exists():
            console.print(f"[red]Error: Video file not found: {video_path}[/red]")
            return
    else:
        r_source = input("Enter source directory path: ").strip()
        if not r_source: return
        recursive_source = _resolve_path(r_source)
        if not recursive_source.is_dir():
            console.print(f"[red]Error: Not a directory: {recursive_source}[/red]")
            return
        params["recursive_source"] = recursive_source
        params["video_filename"] = input(f"Enter video filename pattern (default: {DEFAULT_RECURSIVE_VIDEO_FILENAME}): ").strip() or DEFAULT_RECURSIVE_VIDEO_FILENAME

    mode_menu = [
        {"title": "Motion-based (saves on movement)", "value": "motion"},
        {"title": "Uniform interval (saves X frames)", "value": "uniform"}
    ]
    mode = prompt_toolkit_menu(format_menu_choices(mode_menu, title_field="title", value_field="value"))
    
    if mode == "motion":
        params["diff_threshold"] = float(input(f"Diff threshold (default: {DEFAULT_DIFF_THRESHOLD}): ") or DEFAULT_DIFF_THRESHOLD)
        params["min_interval"] = int(input(f"Min frame interval (default: {DEFAULT_MIN_INTERVAL}): ") or DEFAULT_MIN_INTERVAL)
        params["max_frames"] = int(input(f"Max frames to extract (default: {DEFAULT_MAX_FRAMES}): ") or DEFAULT_MAX_FRAMES)
    else:
        params["count"] = int(input("Number of frames to extract: ") or 10)

    start_val = input("Start timestamp (default: 00:00:00): ") or "00:00:00"
    end_val = input("End timestamp (default: end): ")
    start_ts = _hms_to_seconds(start_val)
    end_ts = _hms_to_seconds(end_val) if end_val else None
    
    output_dir = _resolve_path(input(f"Output directory (default: {os.getcwd()}): ") or ".")
    
    _display_extraction_summary(video_path, output_dir, start_ts, end_ts, mode, params)
    
    if not prompt_yes_no("Proceed with extraction?"):
        return

    if choice == "single":
        if mode == "motion":
            extract_motion_frames(video_path, output_dir, params["diff_threshold"], params["min_interval"], start_ts, end_ts, params["max_frames"])
        else:
            extract_uniform_frames(video_path, output_dir, params["count"], start_ts, end_ts)
    else:
        process_recursive(params["recursive_source"], params["video_filename"], output_dir, mode, params, start_ts, end_ts)

def process_recursive(source_dir, filename, output_dir, mode, params, start_ts, end_ts):
    """Batch process multiple videos."""
    videos = list(source_dir.rglob(filename))
    if not videos:
        console.print(f"[yellow]No videos found matching '{filename}' in {source_dir}[/yellow]")
        return
    
    console.print(f"[bold green]Found {len(videos)} videos to process.[/bold green]")
    for video_path in videos:
        console.print(f"\n[bold cyan]> Processing {video_path.name}[/bold cyan]")
        if mode == "motion":
            extract_motion_frames(video_path, output_dir, params.get("diff_threshold", DEFAULT_DIFF_THRESHOLD), params.get("min_interval", DEFAULT_MIN_INTERVAL), start_ts, end_ts, params.get("max_frames", DEFAULT_MAX_FRAMES))
        else:
            extract_uniform_frames(video_path, output_dir, params.get("count", 10), start_ts, end_ts)

def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="Extract frames from video.")
    parser.add_argument("video", nargs="?", help="Input video file")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("--mode", choices=["motion", "uniform"], help="Extraction mode")
    parser.add_argument("--count", type=int, help="Number of frames for uniform mode")
    parser.add_argument("--from", dest="start", default="00:00:00", help="Start timestamp")
    parser.add_argument("--to", dest="end", help="End timestamp")
    parser.add_argument("--diff-threshold", type=float, default=DEFAULT_DIFF_THRESHOLD)
    parser.add_argument("--min-interval", type=int, default=DEFAULT_MIN_INTERVAL)
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument("--recursive-source", help="Directory for recursive search")
    parser.add_argument("--video-filename", default=DEFAULT_RECURSIVE_VIDEO_FILENAME)
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    
    args = parser.parse_args(argv)

    if not any([args.video, args.recursive_source]) and sys.stdin.isatty():
        interactive_mode()
        return

    if not args.video and not args.recursive_source:
        parser.print_help()
        return

    output_dir = _resolve_path(args.output or ".")
    start_ts = _hms_to_seconds(args.start)
    end_ts = _hms_to_seconds(args.end) if args.end else None
    mode = args.mode or ("uniform" if (args.count or args.max_frames != DEFAULT_MAX_FRAMES and args.mode == "uniform") else "motion")
    
    # If mode is uniform and count is missing but max_frames is provided, use max_frames
    count = args.count
    if mode == "uniform" and count is None:
        if args.max_frames != DEFAULT_MAX_FRAMES:
            count = args.max_frames
        else:
            count = 10  # Default fallback
    
    params = {
        "diff_threshold": args.diff_threshold,
        "min_interval": args.min_interval,
        "max_frames": args.max_frames,
        "count": count,
        "recursive_source": args.recursive_source,
        "video_filename": args.video_filename
    }

    if not args.yes:
        video_path = _resolve_path(args.video) if args.video else None
        _display_extraction_summary(video_path, output_dir, start_ts, end_ts, mode, params)
        if not prompt_yes_no("Proceed?"):
            return

    if args.recursive_source:
        process_recursive(_resolve_path(args.recursive_source), args.video_filename, output_dir, mode, params, start_ts, end_ts)
    else:
        video_path = _resolve_path(args.video)
        if mode == "motion":
            extract_motion_frames(video_path, output_dir, args.diff_threshold, args.min_interval, start_ts, end_ts, args.max_frames)
        else:
            extract_uniform_frames(video_path, output_dir, params["count"], start_ts, end_ts)

if __name__ == "__main__":
    main()
