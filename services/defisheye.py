#!/usr/bin/env python3
"""
@pas-executable
Fisheye camera calibration: checkerboard assets, calibrate from images, test undistort (OpenCV fisheye).

Requires: opencv-python, numpy, PyYAML, fpdf2. Uses GUI windows for --preview and test mode.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

# Add project root to sys.path so we can find 'helpers'
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core import (  # noqa: E402
    console,
    format_menu_choices,
    normalize_path_input,
    prompt_toolkit_menu,
    prompt_yes_no,
)
from rich.panel import Panel  # noqa: E402

# --- Tool identity (pas list, panel, -h) ---
TOOL_ID = "defisheye"
TOOL_TITLE = "Fisheye calibration (checkerboard, calibrate, test)"
TOOL_SHORT_DESC = (
    "Generate checkerboard SVG/PDF, run OpenCV fisheye calibration on images, test undistort on image or video."
)
TOOL_DESCRIPTION = (
    "Three tasks: (1) create — SVG checkerboard + PDF guide; (2) calibrate — chessboard images → camera.yaml; "
    "(3) test — preview undistort with optional balance tuning (saves BALANCE to YAML on change). "
    "Run with a subcommand and flags for non-interactive use, or with no arguments for the menu."
)
# --------------------------

# --- Paths & assets ---
_SCRIPT_DIR = Path(__file__).resolve().parent
CALIBRATION_GUIDE_MD = _SCRIPT_DIR / "defisheye-calibration-guide.md"
# --------------------------

# --- Defaults (checkerboard / calibration) ---
DEFAULT_CHECKER_ROWS = 10
DEFAULT_CHECKER_COLS = 7
DEFAULT_SQUARE_MM = 20.0
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297
CALIBRATION_MIN_IMAGES_RECOMMENDED = 10
PDF_MARGIN_MM = 20.32
# OpenCV subpixel / termination
SUBPIX_CRITERIA = (30, 0.1)
FISHEYE_TERM_CRITERIA = (30, 1e-6)
# --------------------------


def _print_banner() -> None:
    console.clear()
    console.print(f"[bold gold1]{TOOL_TITLE.upper()}[/bold gold1]")
    console.print("[dim]-------------------------------------[/dim]")


def generate_checkerboard_svg(rows: int, cols: int, square_size_mm: float, output_path: Path) -> None:
    """Generate a checkerboard SVG (rows/cols = number of squares)."""
    page_width, page_height = A4_WIDTH_MM, A4_HEIGHT_MM
    pattern_width = cols * square_size_mm
    pattern_height = rows * square_size_mm
    margin_x = (page_width - pattern_width) / 2
    margin_y = (page_height - pattern_height) / 2

    svg = ET.Element(
        "svg",
        {
            "width": f"{page_width}mm",
            "height": f"{page_height}mm",
            "viewBox": f"0 0 {page_width} {page_height}",
            "xmlns": "http://www.w3.org/2000/svg",
        },
    )
    ET.SubElement(svg, "rect", {"width": str(page_width), "height": str(page_height), "fill": "white"})
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 1:
                ET.SubElement(
                    svg,
                    "rect",
                    {
                        "x": str(margin_x + c * square_size_mm),
                        "y": str(margin_y + r * square_size_mm),
                        "width": str(square_size_mm),
                        "height": str(square_size_mm),
                        "fill": "black",
                    },
                )
    tree = ET.ElementTree(svg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)
    console.print(f"[green]✓ Created checkerboard SVG: {output_path}[/green]")


def generate_pdf_guide(square_size_mm: float, output_path: Path, template_path: Path) -> None:
    """Render calibration guide markdown to PDF using fpdf2."""
    from fpdf import FPDF

    if not template_path.is_file():
        console.print(f"[red]✗ Template not found: {template_path}[/red]")
        return

    content = template_path.read_text(encoding="utf-8")
    content = content.replace("{square_size}", str(square_size_mm))
    margin = PDF_MARGIN_MM
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=margin, top=margin, right=margin)
    pdf.set_auto_page_break(auto=True, margin=margin)
    pdf.add_page()

    def render_markdown_line(pdf: Any, text: str, *, size: int = 10, is_header: bool = False) -> None:
        processed = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
        if is_header:
            pdf.multi_cell(0, 10, text)
        else:
            pdf.set_font("Helvetica", size=size)
            pdf.write_html(processed)
            pdf.ln(6)

    template_dir = template_path.parent
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            pdf.ln(5)
            continue
        img_match = re.search(r"!\[.*?\]\((.*?)\)", line)
        if img_match:
            img_rel = img_match.group(1)
            img_full = template_dir / img_rel
            if img_full.is_file():
                try:
                    avail_w = pdf.w - 2 * margin
                    pdf.image(str(img_full), w=avail_w * 0.9)
                    pdf.ln(10)
                except Exception as e:
                    console.print(f"[yellow]! Error embedding image {img_rel}: {e}[/yellow]")
            else:
                console.print(f"[dim]Skipping missing image: {img_full}[/dim]")
            continue
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 20)
            render_markdown_line(pdf, line[2:], is_header=True)
            pdf.ln(4)
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 16)
            render_markdown_line(pdf, line[3:], is_header=True)
            pdf.ln(3)
        elif line.startswith("### "):
            pdf.set_font("Helvetica", "B", 13)
            render_markdown_line(pdf, line[4:], is_header=True)
            pdf.ln(2)
        elif line.startswith("- ") or line.startswith("* "):
            pdf.set_x(margin + 5)
            render_markdown_line(pdf, f"• {line[2:]}")
        elif re.match(r"^\d+\.", line):
            pdf.set_x(margin + 5)
            render_markdown_line(pdf, line)
        else:
            render_markdown_line(pdf, line)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(output_path))
        console.print(f"[green]✓ Created calibration guide PDF: {output_path}[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to save PDF: {e}[/red]")


def calibrate_folder(
    folder_path: Path,
    rows: int,
    cols: int,
    *,
    preview: bool = False,
    force: bool = False,
    interactive_prompt: bool = False,
) -> int:
    """
    Calibrate from images in folder (or folder/inputs). rows/cols = square counts.
    Returns 0 on success, 1 on failure or abort.
    """
    import cv2
    import numpy as np

    pattern_size = (cols - 1, rows - 1)
    console.print(
        f"\n[bold gold1]Targeting {pattern_size[0]}x{pattern_size[1]} inner corners "
        f"(from {cols}x{rows} squares)...[/bold gold1]"
    )
    subpix_criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,) + SUBPIX_CRITERIA
    calibration_flags = (
        cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
        + cv2.fisheye.CALIB_CHECK_COND
        + cv2.fisheye.CALIB_FIX_SKEW
    )
    objp = np.zeros((1, pattern_size[0] * pattern_size[1], 3), np.float32)
    objp[0, :, :2] = np.mgrid[0 : pattern_size[0], 0 : pattern_size[1]].T.reshape(-1, 2)
    objpoints: List[Any] = []
    imgpoints: List[Any] = []

    inputs = folder_path / "inputs"
    search_path = inputs if inputs.is_dir() else folder_path
    if inputs.is_dir():
        console.print(f"[blue]Using images from subfolder: {search_path}[/blue]")

    patterns = ("*.[jJ][pP][gG]", "*.[jJ][pP][eE][gG]", "*.[pP][nN][gG]")
    images: List[str] = []
    for pat in patterns:
        images.extend(glob.glob(str(search_path / pat)))
    images = sorted(images)

    if not images:
        console.print(f"[red]✗ No images found in {search_path}[/red]")
        return 1

    console.print(f"Processing {len(images)} images...")
    img_shape: Optional[Tuple[int, int]] = None
    valid_count = 0
    for fname in images:
        img = cv2.imread(fname)
        if img is None:
            console.print(f"[yellow]! Skipping {fname}: unreadable[/yellow]")
            continue
        if img_shape is None:
            img_shape = img.shape[:2]
        elif img_shape != img.shape[:2]:
            console.print(
                f"[yellow]! Skipping {fname}: inconsistent size "
                f"({img.shape[1]}x{img.shape[0]}, expected {img_shape[1]}x{img_shape[0]})[/yellow]"
            )
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ret, corners = cv2.findChessboardCornersSB(
            gray,
            pattern_size,
            cv2.CALIB_CB_EXHAUSTIVE + cv2.CALIB_CB_ACCURACY,
        )
        if not ret:
            ret, corners = cv2.findChessboardCorners(
                gray,
                pattern_size,
                cv2.CALIB_CB_ADAPTIVE_THRESH
                + cv2.CALIB_CB_NORMALIZE_IMAGE
                + cv2.CALIB_CB_FILTER_QUADS,
            )
        if ret:
            objpoints.append(objp)
            cv2.cornerSubPix(gray, corners, (3, 3), (-1, -1), subpix_criteria)
            imgpoints.append(corners)
            valid_count += 1
            console.print(f"[green]✓ Found corners in {os.path.basename(fname)}[/green]")
            if preview:
                vis = img.copy()
                cv2.drawChessboardCorners(vis, pattern_size, corners, ret)
                display_h = 600
                display_w = int(600 * vis.shape[1] / vis.shape[0])
                cv2.imshow("Detection Preview", cv2.resize(vis, (display_w, display_h)))
                cv2.waitKey(100)
        else:
            console.print(f"[red]✗ No corners found in {os.path.basename(fname)}[/red]")

    if preview:
        cv2.destroyAllWindows()

    if valid_count < CALIBRATION_MIN_IMAGES_RECOMMENDED:
        console.print(
            f"\n[yellow]⚠️ Only {valid_count} valid images; "
            f"{CALIBRATION_MIN_IMAGES_RECOMMENDED}+ recommended.[/yellow]"
        )
        if valid_count == 0:
            console.print("[red]✗ Calibration aborted: no valid images.[/red]")
            return 1
        if force:
            pass
        elif interactive_prompt:
            if not prompt_yes_no("Proceed with calibration anyway?", default=False):
                return 1
        else:
            console.print("[red]✗ Use --force to calibrate with fewer images, or add more captures.[/red]")
            return 1

    assert img_shape is not None
    console.print("\n[bold gold1]Running fisheye calibration...[/bold gold1]")
    K = np.zeros((3, 3))
    D = np.zeros((4, 1))
    rvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in range(valid_count)]
    tvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in range(valid_count)]
    try:
        rms, _, _, _, _ = cv2.fisheye.calibrate(
            objpoints,
            imgpoints,
            img_shape[::-1],
            K,
            D,
            rvecs,
            tvecs,
            calibration_flags,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,) + FISHEYE_TERM_CRITERIA,
        )
        if abs(K[2, 2] - 1.0) > 0.01 or abs(K[2, 0]) > 0.01 or abs(K[2, 1]) > 0.01:
            console.print(f"[yellow]⚠️ K matrix last row is {K[2].tolist()} (expected [0,0,1])[/yellow]")
            K[2, :] = [0, 0, 1]

        output_yaml = folder_path / "camera.yaml"
        data = {
            "DIM": list(img_shape[::-1]),
            "K": [list(row) for row in K.tolist()],
            "D": [[float(d)] for d in D.flatten().tolist()],
            "BALANCE": 0.8,
            "DATE": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "RMS": float(rms),
        }
        import yaml

        with open(output_yaml, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        console.print(f"\n[green]✓ Calibration successful! RMS error: {rms:.4f}[/green]")
        console.print(f"[green]✓ Saved calibration to: {output_yaml}[/green]")
        return 0
    except Exception as e:
        console.print(f"\n[red]✗ Calibration failed: {e}[/red]")
        return 1


def _load_camera_yaml(yaml_path: Path) -> Optional[Tuple[Any, Any, Tuple[int, int], float]]:
    import numpy as np
    import yaml

    with open(yaml_path, encoding="utf-8") as f:
        cam_data = yaml.safe_load(f)
    K = np.array(cam_data["K"], dtype=np.float64)
    if K.shape != (3, 3):
        console.print(f"[red]✗ K must be 3x3, got shape {K.shape}[/red]")
        return None
    if abs(K[2, 2] - 1.0) > 0.01:
        console.print(f"[yellow]⚠️ K[2,2] should be 1.0, got {K[2,2]}[/yellow]")
    D_raw = np.array(cam_data["D"], dtype=np.float64)
    D = D_raw.flatten()
    if len(D) != 4:
        console.print(f"[red]✗ D must have 4 elements, got {len(D)}[/red]")
        return None
    D = D.reshape((4, 1))
    dim_t = tuple(cam_data["DIM"])
    if len(dim_t) != 2:
        console.print(f"[red]✗ DIM must be [width, height], got {dim_t}[/red]")
        return None
    balance = float(cam_data.get("BALANCE", 0.8))
    return K, D, (int(dim_t[0]), int(dim_t[1])), balance


def test_calibration(yaml_path: Path, source_path: Path) -> int:
    """Interactive undistort preview; writes BALANCE to YAML if changed. Returns 0 on success."""
    import cv2
    import numpy as np
    import yaml

    if not yaml_path.is_file():
        console.print(f"[red]✗ Calibration file not found: {yaml_path}[/red]")
        return 1
    result = _load_camera_yaml(yaml_path)
    if result is None:
        return 1
    K, D, DIM, balance = result
    initial_balance = balance

    console.print(
        f"[dim]Loaded: DIM={DIM}, K[0,0]={K[0,0]:.1f}, D={D.flatten().tolist()}, BALANCE={balance}[/dim]"
    )
    is_image = source_path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    if is_image:
        frame = cv2.imread(str(source_path))
        if frame is None:
            console.print(f"[red]✗ Failed to load image: {source_path}[/red]")
            return 1
        img_shape = frame.shape[:2]
        cap = None
    else:
        cap = cv2.VideoCapture(str(source_path))
        if not cap.isOpened():
            console.print(f"[red]✗ Failed to open video: {source_path}[/red]")
            return 1
        ret, frame = cap.read()
        if not ret:
            console.print(f"[red]✗ Failed to read first frame from video: {source_path}[/red]")
            cap.release()
            return 1
        img_shape = frame.shape[:2]
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    dim1 = img_shape[::-1]
    aspect_src = dim1[0] / dim1[1]
    aspect_cal = DIM[0] / DIM[1]
    if abs(aspect_src - aspect_cal) > 0.01:
        console.print("[yellow]⚠️ Aspect ratio mismatch - calibration cannot be applied.[/yellow]")
        console.print(f"  Source: {dim1[0]}x{dim1[1]} (ratio {aspect_src:.3f})")
        console.print(f"  Calibration DIM: {DIM[0]}x{DIM[1]} (ratio {aspect_cal:.3f})")
        console.print("[yellow]Use the same resolution as calibration, or recalibrate.[/yellow]")
        if cap is not None:
            cap.release()
        return 1

    console.print("\n[bold gold1]Testing calibration...[/bold gold1]")
    console.print(f"Source: {dim1[0]}x{dim1[1]}, Calibration DIM: {DIM[0]}x{DIM[1]}")
    console.print("Press [bold]'q'[/bold] to exit, [bold]'u'/'j'[/bold] to adjust balance ([bold]%s[/bold])" % balance)

    def get_maps(bal: float):
        scale = dim1[0] / DIM[0]
        scaled_K = K.astype(np.float64) * scale
        scaled_K[2, 2] = 1.0
        new_K = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
            scaled_K, D, dim1, np.eye(3), balance=bal
        )
        return cv2.fisheye.initUndistortRectifyMap(
            scaled_K, D, np.eye(3), new_K, dim1, cv2.CV_16SC2
        )

    map1, map2 = get_maps(balance)
    while True:
        if is_image:
            display_frame = frame.copy()
        else:
            assert cap is not None
            ret, display_frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
        undistorted = cv2.remap(
            display_frame,
            map1,
            map2,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        cv2.putText(
            undistorted,
            f"Balance: {balance:.2f} (U/J to adjust)",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )
        h, w = undistorted.shape[:2]
        if h > 800 or w > 1200:
            scale = min(800 / h, 1200 / w)
            display_img = cv2.resize(undistorted, (int(w * scale), int(h * scale)))
        else:
            display_img = undistorted
        cv2.imshow("Calibration Test (Undistorted)", display_img)
        key = cv2.waitKey(1 if not is_image else 0) & 0xFF
        if key == ord("q"):
            break
        if key == ord("u"):
            balance = min(1.0, balance + 0.05)
            map1, map2 = get_maps(balance)
        elif key == ord("j"):
            balance = max(0.0, balance - 0.05)
            map1, map2 = get_maps(balance)

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()

    if abs(balance - initial_balance) > 0.001:
        with open(yaml_path, encoding="utf-8") as f:
            cam_data = yaml.safe_load(f)
        cam_data["BALANCE"] = float(round(balance, 3))
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(cam_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        console.print(f"[green]✓ Updated BALANCE to {cam_data['BALANCE']} in {yaml_path}[/green]")
    return 0


def _prompt_numeric(prompt: str, default_val: Any, type_cast: type) -> Any:
    console.print(f"\n[bold cyan]{prompt}[/bold cyan] [dim](default: {default_val})[/dim]: ", end="")
    try:
        result = input().strip()
    except (EOFError, KeyboardInterrupt):
        return default_val
    if not result:
        return default_val
    try:
        return type_cast(result)
    except ValueError:
        console.print(f"[yellow]! Invalid input {result!r}, using default: {default_val}[/yellow]")
        return default_val


def _select_from_list(items: Sequence[str], item_type: str) -> Optional[str]:
    menu_items: List[dict] = [{"title": item, "value": item} for item in items]
    menu_items.append({"title": "Enter path manually", "value": "manual"})
    menu_items.append({"title": "[Back]", "value": "back"})
    menu_items.append({"title": "[Quit]", "value": "quit"})
    formatted = format_menu_choices(menu_items, title_field="title", value_field="value")
    selected = prompt_toolkit_menu(formatted)
    if selected in (None, "quit", "back"):
        return None
    if selected == "manual":
        raw = input(f"Enter path to {item_type}: ").strip()
        path = normalize_path_input(raw)
        p = Path(path).expanduser()
        if not p.exists():
            console.print(f"[red]✗ Path does not exist: {path}[/red]")
            return None
        return str(p.resolve())
    return selected


def cmd_create(args: argparse.Namespace) -> int:
    out = Path(args.output_dir).expanduser().resolve()
    svg_name = out / f"checkerboard-{args.rows}x{args.cols}.svg"
    pdf_name = out / f"checkerboard-{args.rows}x{args.cols}-guide.pdf"
    generate_checkerboard_svg(args.rows, args.cols, args.size, svg_name)
    generate_pdf_guide(args.size, pdf_name, CALIBRATION_GUIDE_MD)
    console.print(f"\n[green]✓ Created {svg_name.name} and {pdf_name.name} in {out}[/green]")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        console.print(f"[red]✗ Not a directory: {folder}[/red]")
        return 1
    return calibrate_folder(
        folder,
        args.rows,
        args.cols,
        preview=args.preview,
        force=args.force,
        interactive_prompt=False,
    )


def cmd_test(args: argparse.Namespace) -> int:
    yml = Path(args.yaml).expanduser().resolve()
    src = Path(args.source).expanduser().resolve()
    if not src.is_file():
        console.print(f"[red]✗ Source not found: {src}[/red]")
        return 1
    return test_calibration(yml, src)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_ID,
        description=TOOL_DESCRIPTION,
    )
    sub = parser.add_subparsers(dest="command", help="Task to run")

    p_create = sub.add_parser("create", help="Write checkerboard SVG + calibration guide PDF")
    p_create.add_argument("--rows", type=int, default=DEFAULT_CHECKER_ROWS, help="Rows (squares)")
    p_create.add_argument("--cols", type=int, default=DEFAULT_CHECKER_COLS, help="Columns (squares)")
    p_create.add_argument("--size", type=float, default=DEFAULT_SQUARE_MM, help="Square size (mm)")
    p_create.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory for SVG and PDF (default: cwd)",
    )
    p_create.set_defaults(func=cmd_create)

    p_cal = sub.add_parser("calibrate", help="Fisheye calibrate from images in folder (or folder/inputs)")
    p_cal.add_argument("--folder", type=Path, required=True, help="Folder containing captures")
    p_cal.add_argument("--rows", type=int, default=DEFAULT_CHECKER_ROWS, help="Rows (squares)")
    p_cal.add_argument("--cols", type=int, default=DEFAULT_CHECKER_COLS, help="Columns (squares)")
    p_cal.add_argument(
        "--preview",
        action="store_true",
        help="OpenCV windows for corner detection preview",
    )
    p_cal.add_argument(
        "--force",
        action="store_true",
        help="Allow calibration with fewer than recommended valid images",
    )
    p_cal.set_defaults(func=cmd_calibrate)

    p_test = sub.add_parser("test", help="Preview undistort (image or video) using camera YAML")
    p_test.add_argument("--yaml", type=Path, required=True, help="Path to camera.yaml")
    p_test.add_argument("--source", type=Path, required=True, help="Image or video path")
    p_test.set_defaults(func=cmd_test)

    return parser


def _interactive_loop() -> None:
    while True:
        _print_banner()
        menu = [
            {"title": "Create checkerboard (SVG + PDF guide)", "value": "create"},
            {"title": "Calibrate from folder of images", "value": "calibrate"},
            {"title": "Test calibration (image or video + YAML)", "value": "test"},
            {"title": "[Quit]", "value": "quit"},
        ]
        choice_val = prompt_toolkit_menu(format_menu_choices(menu))
        if choice_val is None or choice_val == "quit":
            console.print("[gold1]Goodbye![/gold1]")
            return

        if choice_val == "create":
            console.print("\n[bold gold1]Create calibration checkerboard[/bold gold1]")
            rows = int(_prompt_numeric("Number of rows (squares)", DEFAULT_CHECKER_ROWS, int))
            cols = int(_prompt_numeric("Number of columns (squares)", DEFAULT_CHECKER_COLS, int))
            sq = float(_prompt_numeric("Square size in mm", DEFAULT_SQUARE_MM, float))
            out_raw = input("\nOutput directory [Enter for cwd]: ").strip()
            out_dir = Path(normalize_path_input(out_raw) if out_raw else ".").expanduser().resolve()
            ns = argparse.Namespace(rows=rows, cols=cols, size=sq, output_dir=out_dir)
            cmd_create(ns)
            input("\nPress Enter to continue...")
        elif choice_val == "calibrate":
            console.print("\n[bold gold1]Calibrate folder[/bold gold1]")
            cwd_dirs = sorted(
                d for d in os.listdir(".") if os.path.isdir(d) and not d.startswith(".")
            )
            folder_s = _select_from_list(cwd_dirs, "folder")
            if not folder_s:
                continue
            console.print("\n[yellow]Enter SQUARE counts on the board (e.g. 10 rows × 7 cols).[/yellow]")
            rows = int(_prompt_numeric("Number of rows (squares)", DEFAULT_CHECKER_ROWS, int))
            cols = int(_prompt_numeric("Number of columns (squares)", DEFAULT_CHECKER_COLS, int))
            preview = prompt_yes_no("Show corner-detection preview windows?", default=False)
            calibrate_folder(
                Path(folder_s),
                rows,
                cols,
                preview=preview,
                force=False,
                interactive_prompt=True,
            )
            input("\nPress Enter to continue...")
        else:
            console.print("\n[bold gold1]Test calibration[/bold gold1]")
            yaml_files = sorted(glob.glob("**/*.yaml", recursive=True))
            yf = _select_from_list(yaml_files, "camera YAML file")
            if not yf:
                continue
            exts = ("*.mp4", "*.ts", "*.avi", "*.jpg", "*.jpeg", "*.png")
            media: List[str] = []
            for ext in exts:
                media.extend(glob.glob(f"**/{ext}", recursive=True))
                media.extend(glob.glob(f"**/{ext.upper()}", recursive=True))
            media = sorted(set(media))
            src = _select_from_list(media, "media source")
            if not src:
                continue
            test_calibration(Path(yf), Path(src))
            input("\nPress Enter to continue...")


def main() -> None:
    parser = _build_parser()
    if len(sys.argv) <= 1:
        console.print(
            Panel(
                f"{TOOL_DESCRIPTION}\n\n"
                f"CLI: [bold]{TOOL_ID} create|calibrate|test …[/bold]  See [bold]-h[/bold] per subcommand.\n"
                "Dependencies: opencv-python, numpy, PyYAML, fpdf2.",
                title=TOOL_TITLE,
                border_style="blue",
            )
        )
        _interactive_loop()
        return

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(2)
    code = args.func(args)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
