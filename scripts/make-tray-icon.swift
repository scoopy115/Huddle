// Render an SVG glyph to a transparent PNG for use as a macOS menu-bar template icon.
// Usage: swift scripts/make-tray-icon.swift <in.svg> <out.png> <size-px> [glyph-fraction]
// The glyph is filled black; only the alpha channel matters for a template image.
import AppKit

let a = CommandLine.arguments
guard a.count >= 4, let size = Int(a[3]) else { print("usage: make-tray-icon.swift in.svg out.png size [fraction]"); exit(64) }
let fraction = a.count > 4 ? Double(a[4]) ?? 0.8 : 0.8
guard let svg = NSImage(contentsOfFile: a[1]) else { print("cannot read \(a[1])"); exit(1) }
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                           hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
rep.size = NSSize(width: size, height: size)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
// Fit the glyph's own bounding box (not the SVG canvas) into `fraction` of the square.
let box = svg.size
let scale = Double(size) * fraction / Double(max(box.width, box.height))
let w = Double(box.width) * scale, h = Double(box.height) * scale
svg.draw(in: NSRect(x: (Double(size) - w) / 2, y: (Double(size) - h) / 2, width: w, height: h), from: .zero, operation: .sourceOver, fraction: 1)
NSGraphicsContext.restoreGraphicsState()
// Force black + alpha.
if let data = rep.bitmapData {
    let n = size * size
    for i in 0..<n { data[i * 4] = 0; data[i * 4 + 1] = 0; data[i * 4 + 2] = 0 }
    var transparent = 0
    for i in 0..<n where data[i * 4 + 3] == 0 { transparent += 1 }
    print("transparent pixels: \(transparent * 100 / n)%")
}
let png = rep.representation(using: .png, properties: [:])!
try! png.write(to: URL(fileURLWithPath: a[2]))
print("wrote \(a[2])")
