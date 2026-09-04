// Render the menu-bar icon states next to the base "h" glyph (scripts/make-tray-icon.swift):
//   tray/recording-light.png  black h + red dot   (non-template, for a light menu bar)
//   tray/recording-dark.png   white h + red dot   (non-template, for a dark menu bar)
//   tray/recording-off.png    h alone, same width (template; the "off" phase of the blink)
//   tray/busy-0..7.png        h + rotating arc     (template)
// Usage: swift scripts/make-tray-states.swift <glyph.svg> <out-dir>
import AppKit

let a = CommandLine.arguments
guard a.count >= 3, let svg = NSImage(contentsOfFile: a[1]) else { print("usage: make-tray-states.swift glyph.svg out-dir"); exit(64) }
let W = 52, H = 36, glyphBox = 36, fraction = 0.82
let badgeX = 43.0, badgeY = 18.0

func render(_ name: String, fg: NSColor, badge: (NSGraphicsContext) -> Void) {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: W, pixelsHigh: H, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: W, height: H)
    NSGraphicsContext.saveGraphicsState()
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = ctx
    ctx.imageInterpolation = .high
    let box = svg.size
    let scale = Double(glyphBox) * fraction / Double(max(box.width, box.height))
    let w = Double(box.width) * scale, h = Double(box.height) * scale
    svg.draw(in: NSRect(x: (Double(glyphBox) - w) / 2, y: (Double(H) - h) / 2, width: w, height: h), from: .zero, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    // Recolour the glyph (only alpha came from the SVG) before drawing the coloured badge.
    if let d = rep.bitmapData, let c = fg.usingColorSpace(.sRGB) {
        let r = UInt8(c.redComponent * 255), g = UInt8(c.greenComponent * 255), b = UInt8(c.blueComponent * 255)
        for i in 0..<(W * H) { d[i * 4] = r; d[i * 4 + 1] = g; d[i * 4 + 2] = b }
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    badge(ctx)
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[2] + "/" + name + ".png"))
    print("wrote \(name).png")
}

let red = NSColor(srgbRed: 0xea / 255, green: 0x3d / 255, blue: 0x3d / 255, alpha: 1)
let dot: (NSGraphicsContext) -> Void = { _ in
    red.setFill()
    NSBezierPath(ovalIn: NSRect(x: badgeX - 5.5, y: badgeY - 5.5, width: 11, height: 11)).fill()
}
render("recording-light", fg: .black, badge: dot)
render("recording-dark", fg: .white, badge: dot)
render("recording-off", fg: .black) { _ in }
for i in 0..<8 {
    render("busy-\(i)", fg: .black) { _ in
        NSColor.black.setStroke()
        let p = NSBezierPath()
        let start = CGFloat(90 - i * 45)
        p.appendArc(withCenter: NSPoint(x: badgeX, y: badgeY), radius: 6.5, startAngle: start, endAngle: start - 270, clockwise: true)
        p.lineWidth = 2.6
        p.lineCapStyle = .round
        p.stroke()
    }
}
