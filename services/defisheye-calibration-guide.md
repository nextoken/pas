# Camera Calibration Checkerboard Guide

## Printing or Digital Display
### Option A: Digital Display (Easiest & Preferred)
You can simply display the generated **SVG** file on a laptop or tablet screen (e.g., iPad). This is often easier than printing and ensures a perfectly flat surface.
1. **Full Brightness**: Set your screen to maximum brightness.
2. **No Scaling**: Ensure the browser or viewer is at **100% zoom** so the squares are not distorted.
3. **Avoid Glare**: Tilt the screen carefully to avoid reflection of light sources into the camera lens.

### Option B: Physical Printout
1. **Actual Size**: When printing the generated SVG or PDF, ensure that the "Scale" is set to **100%** or **Actual Size**. Do NOT use "Fit to Page" or any scaling options, as this will change the square dimensions and invalidate the calibration.
2. **High Contrast**: Use a high-quality printer with good black ink/toner. The corners between black and white squares must be sharp and well-defined.
3. **Flat Surface**: Mount the printed checkerboard on a very flat, rigid surface (like a piece of foam board, glass, or a flat clipboard). Any curvature in the paper will lead to an inaccurate camera model.

## Capture Tips for Fisheye Calibration
1. **Multiple Angles**: Take at least 10-20 images of the checkerboard.
2. **Cover the Frame**: Move the checkerboard to different parts of the camera's view, especially the corners and edges where fisheye distortion is most severe.
3. **Tilt Variation**: Tilt the checkerboard at various angles (up/down, left/right) relative to the camera. Do not just keep it parallel to the lens.
4. **Lighting**: Ensure even lighting without harsh glares or deep shadows on the pattern.
5. **Focus**: The pattern must be in sharp focus in every image.

## Square Size Reference
The generated checkerboard uses a square size of **{square_size}mm**. This value is important for the calibration algorithm to understand physical scale.
