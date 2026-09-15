// Render the drag-to-Applications background of the Huddle disk image.
// Usage: swift scripts/make-dmg-background.swift <huddle-logo.svg> <out-dir>
// Writes background.png (1×), background@2x.png and background.tiff (both, for Retina Finder).
// The Finder window is 660×420 points; the app icon sits at (165, 190) and the Applications
// alias at (495, 190) — scripts/make-dmg.sh places the icons at exactly those points, so the
// arrow and the labels drawn here line up with them.
import AppKit

let a = CommandLine.arguments
guard a.count >= 3, let logo = NSImage(contentsOfFile: a[1]) else { print("usage: make-dmg-background.swift logo.svg out-dir"); exit(64) }
let outDir = a[2]
let W = 660.0, H = 420.0
let paper = NSColor(srgbRed: 0.976, green: 0.973, blue: 0.965, alpha: 1)     // #f9f8f6
let ink = NSColor(srgbRed: 0.110, green: 0.098, blue: 0.094, alpha: 1)       // #1c1918
let red = NSColor(srgbRed: 0.918, green: 0.239, blue: 0.239, alpha: 1)       // #ea3d3d
let iconY = 190.0, appX = 165.0, appsX = 495.0, icon = 128.0

func render(scale: Double) -> NSBitmapImageRep {
    let pw = Int(W * scale), ph = Int(H * scale)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pw, pixelsHigh: ph, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: W, height: H)
    NSGraphicsContext.saveGraphicsState()
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = ctx
    ctx.imageInterpolation = .high
    // rep.size (points) vs. pixelsWide (pixels) already gives the context its 1× / 2× scale.
    // Finder's coordinate origin is top-left; flip so the numbers below read top-down.
    ctx.cgContext.translateBy(x: 0, y: H)
    ctx.cgContext.scaleBy(x: 1, y: -1)

    paper.setFill()
    NSRect(x: 0, y: 0, width: W, height: H).fill()
    // The same soft red wash the sidebar has behind the wordmark.
    let wash = NSGradient(colors: [red.withAlphaComponent(0.13), red.withAlphaComponent(0.0)])!
    wash.draw(in: NSBezierPath(rect: NSRect(x: 0, y: 0, width: W, height: H)), relativeCenterPosition: NSPoint(x: 0, y: 1))

    // Wordmark, top centre (NSImage draws in the flipped context, so flip it back locally).
    let lw = 190.0, lh = lw * logo.size.height / logo.size.width
    ctx.cgContext.saveGState()
    ctx.cgContext.translateBy(x: (W - lw) / 2, y: 34 + lh)
    ctx.cgContext.scaleBy(x: 1, y: -1)
    logo.draw(in: NSRect(x: 0, y: 0, width: lw, height: lh), from: .zero, operation: .sourceOver, fraction: 1)
    ctx.cgContext.restoreGState()

    // Two faint rings mark where the icons land; a red arrow points the way.
    for x in [appX, appsX] {
        let ring = NSBezierPath(ovalIn: NSRect(x: x - icon / 2 - 14, y: iconY - icon / 2 - 14, width: icon + 28, height: icon + 28))
        ring.lineWidth = 1.5
        ink.withAlphaComponent(0.10).setStroke()
        ring.stroke()
    }
    let ax0 = appX + icon / 2 + 34, ax1 = appsX - icon / 2 - 34
    let arrow = NSBezierPath()
    arrow.lineWidth = 5
    arrow.lineCapStyle = .round
    arrow.lineJoinStyle = .round
    arrow.move(to: NSPoint(x: ax0, y: iconY))
    arrow.line(to: NSPoint(x: ax1, y: iconY))
    arrow.move(to: NSPoint(x: ax1 - 18, y: iconY - 16))
    arrow.line(to: NSPoint(x: ax1, y: iconY))
    arrow.line(to: NSPoint(x: ax1 - 18, y: iconY + 16))
    red.setStroke()
    arrow.stroke()

    // Text. The flipped context would mirror glyphs, so draw strings through an unflipped block.
    func text(_ s: String, size: CGFloat, weight: NSFont.Weight, color: NSColor, y: Double, rounded: Bool = false) {
        var font = NSFont.systemFont(ofSize: size, weight: weight)
        if rounded, let d = font.fontDescriptor.withDesign(.rounded), let f = NSFont(descriptor: d, size: size) { font = f }
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
        let str = NSAttributedString(string: s, attributes: attrs)
        let sz = str.size()
        ctx.cgContext.saveGState()
        ctx.cgContext.translateBy(x: (W - sz.width) / 2, y: y + sz.height)
        ctx.cgContext.scaleBy(x: 1, y: -1)
        str.draw(at: .zero)
        ctx.cgContext.restoreGState()
    }
    text("Drag Huddle into Applications", size: 17, weight: .bold, color: ink, y: 304, rounded: true)
    NSGraphicsContext.restoreGraphicsState()
    return rep
}

let one = render(scale: 1), two = render(scale: 2)
try! one.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(outDir)/background.png"))
try! two.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(outDir)/background@2x.png"))
print("wrote \(outDir)/background.png and background@2x.png")
