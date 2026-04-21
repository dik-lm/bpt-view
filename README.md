# BPT-View

A lightweight Python viewer for `.BPT` tomography files, with support for:

- axial, coronal, and sagittal MPR
- frontal MIP
- curved panoramic reconstruction (curved MPR)
- brightness/contrast adjustment
- visual Z aspect adjustment
- movable panel dividers
- export of rendered slices

## Features

- Structured `.BPT` parser
- Async volume loading with responsive UI
- Axial / Coronal / Sagittal views
- Frontal MIP view
- Curved panoramic reconstruction from user-defined spline
- Crosshair toggle
- Per-slice export (axial, coronal, sagittal)
- Window/level controls
- Resizable split panes

## Requirements

Install dependencies with:

```bash
pip install numpy imagecodecs Pillow scipy
````

Or from `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Usage

Run without arguments to choose a file manually:

```bash
python bpt_viewer.py
```

Run with a `.BPT` file path:

```bash
python bpt_viewer.py path/to/exam.bpt
```

## About the `.BPT` format

This project includes a parser for the `.BPT` container layout observed during development.

Observed structure:

* 64-byte header (`16 × uint32`, little-endian)
* first JPEG slice stored without a size prefix
* remaining slices stored as:

  * `[uint32 little-endian size]`
  * `[JPEG payload]`
* optional undocumented trailing bytes after the last slice

Mapped header fields:

|   Index | Field              | Type         |
| ------: | ------------------ | ------------ |
|  `h[6]` | `width`            | `uint32`     |
|  `h[7]` | `height`           | `uint32`     |
|  `h[8]` | `num_slices`       | `uint32`     |
|  `h[9]` | `spacing_x`        | `float32 LE` |
| `h[10]` | `spacing_y`        | `float32 LE` |
| `h[11]` | `spacing_z`        | `float32 LE` |
| `h[15]` | first slice length | `uint32`     |

## Technical notes

### Display geometry

Rendering and click mapping use the same geometry function, which keeps interaction consistent across panels.

### MIP consistency

The frontal MIP follows the same internal display convention used by coronal and sagittal views, keeping crosshair and click mapping aligned.

### Curved panoramic reconstruction

The panoramic view is reconstructed from a spline drawn in the axial view and sampled in physical space, using interpolated slab MIP along the curve.

### Z aspect

The viewer exposes a visual Z aspect control for display.
The default display preset is `1.5×`, while the physical aspect ratio derived from spacing is still tracked internally.

## Known limitations

* The implementation is based on the `.BPT` structure observed in this project. Other `.BPT` variants may not work.
* Panoramic reconstruction runs on the UI thread and may briefly pause the interface on larger volumes.
* The panoramic window does not auto-update when window/level changes; it must be recalculated.
* No distance measurement tools are included.
* No keyboard shortcuts are currently implemented.

## Disclaimer

This project is a personal/technical viewer for `.BPT` tomography files.

It is:

* not a certified medical workstation
* not intended for official clinical diagnosis
* not a replacement for approved medical imaging software

## License

MIT

## Portuguese version

For a Portuguese version of this documentation, see:

`README.pt-BR.md`
