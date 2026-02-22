#!/usr/bin/env python3
"""
@pas-executable
Identify the YOLO version and task type from a PyTorch (.pt) model file.
"""

import sys
import argparse
from pathlib import Path
from rich.panel import Panel

# Import helpers for consistent UI
try:
    from services.helpers import console
except ImportError:
    # Fallback if helpers are not available (though they should be in this toolkit)
    from rich.console import Console
    console = Console()

def detect_yolo_version_and_task(pt_path: str):
    """
    Analyzes the checkpoint and model structure to infer YOLO version and task.
    """
    try:
        import torch
        from ultralytics import YOLO
    except ImportError:
        return "Error: 'torch' and 'ultralytics' packages are required. Install them with: pip install torch ultralytics"

    path = Path(pt_path).expanduser().resolve()
    if not path.exists():
        return f"Error: File not found at {path}"

    try:
        # Load checkpoint to check for training arguments
        # PyTorch 2.6+ defaults to weights_only=True, which fails for YOLO models.
        try:
            ckpt = torch.load(path, map_location="cpu", weights_only=False)
        except TypeError:
            # Fallback for older torch versions where weights_only is not an argument
            ckpt = torch.load(path, map_location="cpu")
    except Exception as e:
        return f"Error loading checkpoint with torch: {e}"

    version = "Unknown"
    task = "Unknown"

    # Step 1: Check for explicit version info in checkpoint metadata
    if isinstance(ckpt, dict) and "train_args" in ckpt and "version" in ckpt["train_args"]:
        version = f"YOLO{ckpt['train_args']['version']}"

    # Step 2: Load with Ultralytics for deeper structural analysis
    try:
        model = YOLO(str(path))
        # The underlying torch model
        m = model.model
        
        # Try to get the last layer (head) safely as DetectionModel may not be subscriptable
        try:
            head_layer = m[-1]
        except (TypeError, KeyError, IndexError, AttributeError):
            try:
                # Many Ultralytics models store the sequential layers in .model
                head_layer = m.model[-1]
            except (TypeError, KeyError, IndexError, AttributeError):
                # Fallback to the last child module
                head_layer = list(m.children())[-1]
        
        head = str(type(head_layer))
        # String representation of all layers to check for specific modules
        layers = str(m)
    except Exception as e:
        return f"Error analyzing model structure: {e}"

    # Step 3: Infer version from head/backbone if not explicitly found
    if "DetectE2E" in head:
        version = "YOLO26"
    elif "Detect" in head:
        if "CSPDarknet" in layers:
            version = "YOLOv5"
        elif ("C2f" in layers or "C3k2" in layers) and "SPPF" in layers:
            # YOLO11 often uses C3k2, YOLOv8 uses C2f
            if "C3k2" in layers:
                version = "YOLO11"
            else:
                version = "YOLOv8"
        else:
            version = "YOLOv8"

    # Step 4: Identify task type
    if hasattr(model, "task") and model.task:
        task = model.task  # Ultralytics sets this internally (detect, segment, etc.)
    else:
        # Fallback: infer from head class name
        if "Detect" in head or "DetectE2E" in head:
            task = "detect"
        elif "Segmentation" in head:
            task = "segment"
        elif "Classification" in head:
            task = "classify"
        elif "Pose" in head:
            task = "pose"
        elif "Oriented" in head:
            task = "oriented detect"

    return f"{version} model trained for {task}"

def main():
    parser = argparse.ArgumentParser(description="Analyze a YOLO .pt file to determine its version and task.")
    parser.add_argument("model", help="Path to the .pt model file", nargs="?")
    args = parser.parse_args()

    # Self-documentation summary
    console.print(Panel.fit(
        "[bold cyan]YOLO Version Detector[/bold cyan]\n\n"
        "This tool identifies the specific YOLO version and training task\n"
        "by inspecting the model's internal architecture and metadata.\n\n"
        "Supported: YOLOv5, YOLOv8, YOLOv10 (YOLO26), YOLO11",
        title="PAS Toolkit"
    ))

    if not args.model:
        console.print("[yellow]Usage: yolo-version <path_to_model.pt>[/yellow]")
        sys.exit(1)

    with console.status("[bold green]Analyzing model...[/bold green]"):
        result = detect_yolo_version_and_task(args.model)

    if result.startswith("Error"):
        console.print(f"\n[bold red]{result}[/bold red]")
        sys.exit(1)
    else:
        console.print(f"\n[bold green]Result:[/bold green] {result}")

if __name__ == "__main__":
    main()
