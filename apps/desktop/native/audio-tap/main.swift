// huddle-audio-tap — records system audio with ScreenCaptureKit (macOS 13+), no driver.
//
//   huddle-audio-tap check              → prints "granted" | "denied" (Screen & System Audio Recording permission)
//   huddle-audio-tap request            → triggers the macOS permission prompt, prints result
//   huddle-audio-tap record <out.wav>   → writes 16-bit mono 48 kHz WAV until stdin closes or SIGTERM
//
// The WAV header is rewritten every second so a crash still leaves a playable file.
// Audio of the current process is excluded, so Huddle's own playback is never recorded.
import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

let sampleRate: Double = 48000

final class WavWriter {
    let handle: FileHandle
    var dataLen: UInt32 = 0
    var lastPatch = Date()
    init(path: String) throws {
        FileManager.default.createFile(atPath: path, contents: nil)
        handle = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        handle.write(header(dataLen: 0))
    }
    func header(dataLen: UInt32) -> Data {
        var d = Data()
        func u32(_ v: UInt32) { var x = v.littleEndian; d.append(Data(bytes: &x, count: 4)) }
        func u16(_ v: UInt16) { var x = v.littleEndian; d.append(Data(bytes: &x, count: 2)) }
        d.append("RIFF".data(using: .ascii)!); u32(36 + dataLen); d.append("WAVE".data(using: .ascii)!)
        d.append("fmt ".data(using: .ascii)!); u32(16); u16(1); u16(1); u32(UInt32(sampleRate)); u32(UInt32(sampleRate) * 2); u16(2); u16(16)
        d.append("data".data(using: .ascii)!); u32(dataLen)
        return d
    }
    func append(_ pcm: Data) {
        handle.seekToEndOfFile()
        handle.write(pcm)
        dataLen += UInt32(pcm.count)
        if Date().timeIntervalSince(lastPatch) >= 1 { patch(); lastPatch = Date() }
    }
    func patch() {
        handle.seek(toFileOffset: 0)
        handle.write(header(dataLen: dataLen))
        try? handle.synchronize()
    }
    func close() { patch(); try? handle.close() }
}

final class Output: NSObject, SCStreamOutput, SCStreamDelegate {
    let writer: WavWriter
    var lastLevel = Date()
    init(writer: WavWriter) { self.writer = writer }

    var audioBuffers = 0
    var screenFrames = 0
    let debug = ProcessInfo.processInfo.environment["HUDDLE_TAP_DEBUG"] != nil

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        if type != .audio {
            screenFrames += 1
            if debug && screenFrames % 25 == 1 { FileHandle.standardError.write("screen frames \(screenFrames), audio buffers \(audioBuffers)\n".data(using: .utf8)!) }
            return
        }
        guard sb.isValid else { return }
        guard let fmt = sb.formatDescription, let asbd = fmt.audioStreamBasicDescription else { return }
        audioBuffers += 1
        if debug && audioBuffers == 1 { FileHandle.standardError.write("first audio buffer: \(asbd.mSampleRate) Hz, \(asbd.mChannelsPerFrame) ch, flags \(asbd.mFormatFlags), \(sb.numSamples) frames\n".data(using: .utf8)!) }
        let channels = Int(asbd.mChannelsPerFrame)
        let nonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        // Non-interleaved audio (what ScreenCaptureKit delivers) needs one AudioBuffer per channel.
        // A single-entry AudioBufferList makes this call fail with "array too small" and every
        // buffer would be dropped silently — the WAV then stays at its 44-byte header.
        let bufferCount = nonInterleaved ? max(1, channels) : 1
        let buffers = AudioBufferList.allocate(maximumBuffers: bufferCount)
        defer { free(buffers.unsafeMutablePointer) }
        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: nil, bufferListOut: buffers.unsafeMutablePointer,
            bufferListSize: AudioBufferList.sizeInBytes(maximumBuffers: bufferCount),
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: &blockBuffer)
        guard status == noErr else {
            if debug { FileHandle.standardError.write("audio buffer list failed: \(status)\n".data(using: .utf8)!) }
            return
        }
        let frames = Int(sb.numSamples)
        var out = [Int16](repeating: 0, count: frames)
        var peak: Float = 0
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        if isFloat {
            if nonInterleaved {
                // one buffer per channel
                for f in 0..<frames {
                    var acc: Float = 0
                    for c in 0..<min(channels, buffers.count) {
                        let p = buffers[c].mData!.assumingMemoryBound(to: Float.self)
                        acc += p[f]
                    }
                    let v = max(-1, min(1, acc / Float(max(1, channels))))
                    peak = max(peak, abs(v)); out[f] = Int16(v * 32767)
                }
            } else {
                let p = buffers[0].mData!.assumingMemoryBound(to: Float.self)
                for f in 0..<frames {
                    var acc: Float = 0
                    for c in 0..<channels { acc += p[f * channels + c] }
                    let v = max(-1, min(1, acc / Float(max(1, channels))))
                    peak = max(peak, abs(v)); out[f] = Int16(v * 32767)
                }
            }
        } else {
            let p = buffers[0].mData!.assumingMemoryBound(to: Int16.self)
            for f in 0..<frames {
                var acc: Int32 = 0
                for c in 0..<channels { acc += Int32(p[f * channels + c]) }
                out[f] = Int16(acc / Int32(max(1, channels)))
            }
        }
        writer.append(Data(bytes: out, count: frames * 2))
        if Date().timeIntervalSince(lastLevel) > 0.2 {
            lastLevel = Date()
            print("level \(peak)"); fflush(stdout)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write("stream stopped: \(error.localizedDescription)\n".data(using: .utf8)!)
        writer.close()
        exit(3)
    }
}

func fail(_ msg: String, code: Int32) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: huddle-audio-tap check|request|record <out.wav>", code: 64) }

switch args[1] {
case "check":
    print(CGPreflightScreenCaptureAccess() ? "granted" : "denied"); exit(0)
case "request":
    let ok = CGRequestScreenCaptureAccess()
    print(ok ? "granted" : "denied"); exit(0)
case "record":
    guard args.count >= 3 else { fail("missing output path", code: 64) }
    let outPath = args[2]
    guard #available(macOS 13.0, *) else { fail("System audio capture needs macOS 13 or newer.", code: 5) }
    if !CGPreflightScreenCaptureAccess() {
        _ = CGRequestScreenCaptureAccess()
        fail("permission-denied", code: 2)
    }
    let writer: WavWriter
    do { writer = try WavWriter(path: outPath) } catch { fail("cannot open output: \(error)", code: 4) }
    let output = Output(writer: writer)
    var streamRef: SCStream?
    let sem = DispatchSemaphore(value: 0)
    Task {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let display = content.displays.first else { fail("no display", code: 6) }
            let filter = SCContentFilter(display: display, excludingWindows: [])
            let cfg = SCStreamConfiguration()
            cfg.capturesAudio = true
            cfg.excludesCurrentProcessAudio = true
            cfg.sampleRate = Int(sampleRate)
            cfg.channelCount = 2
            // ScreenCaptureKit only delivers audio while a live video pipeline exists: a 2×2 frame
            // at 1 fps with no screen output starts fine but never produces a single audio buffer
            // (macOS 26.6). A small, low-rate video stream whose frames we throw away costs ~nothing.
            let env = ProcessInfo.processInfo.environment
            cfg.width = Int(env["HUDDLE_TAP_W"] ?? "") ?? 64
            cfg.height = Int(env["HUDDLE_TAP_H"] ?? "") ?? 36
            cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(Int(env["HUDDLE_TAP_FPS"] ?? "") ?? 5))
            cfg.queueDepth = 3
            cfg.showsCursor = false
            if env["HUDDLE_TAP_DEBUG"] != nil {
                FileHandle.standardError.write("display \(display.displayID) \(display.width)x\(display.height); config \(cfg.width)x\(cfg.height), excludesCurrentProcessAudio=\(cfg.excludesCurrentProcessAudio)\n".data(using: .utf8)!)
            }
            let stream = SCStream(filter: filter, configuration: cfg, delegate: output)
            try stream.addStreamOutput(output, type: .screen, sampleHandlerQueue: DispatchQueue(label: "huddle.video"))
            try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "huddle.audio"))
            try await stream.startCapture()
            streamRef = stream
            print("READY"); fflush(stdout)
        } catch {
            fail("start failed: \(error.localizedDescription)", code: 3)
        }
        sem.signal()
    }
    sem.wait()
    // Run until stdin closes (parent dropped the pipe) or SIGTERM.
    let stopAndExit: () -> Void = {
        let done = DispatchSemaphore(value: 0)
        Task { try? await streamRef?.stopCapture(); done.signal() }
        _ = done.wait(timeout: .now() + 3)
        writer.close()
        exit(0)
    }
    signal(SIGTERM) { _ in exit(0) }
    signal(SIGINT) { _ in exit(0) }
    DispatchQueue.global().async {
        while let line = readLine() { if line == "stop" { break } }
        stopAndExit()
    }
    RunLoop.main.run()
default:
    fail("unknown command", code: 64)
}
