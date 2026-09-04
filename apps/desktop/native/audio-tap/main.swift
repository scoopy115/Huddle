// huddle-audio-tap — records system audio through a Core Audio process tap (macOS 14.2+), no
// driver, no screen recording. The only permission involved is "System Audio Recording Only"
// (NSAudioCaptureUsageDescription), which macOS asks for the first time a tap is created.
//
//   huddle-audio-tap check              → prints "granted" | "denied" (silent probe, see below; prompts if undetermined)
//   huddle-audio-tap request            → creates a tap for a moment so macOS asks for permission (once); prints "unknown"
//   huddle-audio-tap mic-check          → prints "granted" | "denied" | "undetermined" (Microphone permission)
//   huddle-audio-tap mic-request        → asks for Microphone permission, prints "granted" | "denied"
//   huddle-audio-tap record <out.wav>   → writes 16-bit mono 48 kHz WAV until stdin closes or SIGTERM
//
// macOS has no API to read the system-audio permission back, and a tap without permission delivers
// silence rather than an error. `check` therefore probes: it runs a tap for 0.5 s while playing a
// 1 kHz tone at −90 dBFS through the default output. That is below what any DAC or speaker can
// reproduce (16-bit quantisation sits at −96 dBFS), but it is a non-zero float sample to the tap,
// so hearing anything at all means capture works. The app remembers a "granted" answer, so the
// probe normally runs once. The WAV header is rewritten every second so a crash still leaves a
// playable file.
import Foundation
import AVFoundation
import CoreAudio

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

func fail(_ msg: String, code: Int32) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

struct TapError: Error, CustomStringConvertible { let description: String; init(_ d: String) { description = d } }

// MARK: - Core Audio process tap

@available(macOS 14.2, *)
final class TapRecorder {
    var tapID = AudioObjectID(kAudioObjectUnknown)
    var aggID = AudioObjectID(kAudioObjectUnknown)
    var procID: AudioDeviceIOProcID?
    var writer: WavWriter?
    var format = AudioStreamBasicDescription()
    var lastLevel = Date()
    /// Loudest sample seen so far (the probe's evidence that capture works).
    var maxAbs: Float = 0
    /// The rate the aggregate device actually runs at. It follows the tapped output device, and
    /// the built-in speakers share a clock with the built-in microphone — so it changes when the
    /// microphone capture starts (48 → 24 kHz on a MacBook). The output stays 48 kHz; frames are
    /// resampled whenever the two differ.
    var deviceRate: Double = 48000
    var callbacks = 0
    var prev: Float = 0
    var phase: Double = 0
    let debug = ProcessInfo.processInfo.environment["HUDDLE_TAP_DEBUG"] != nil

    func start(outPath: String?) throws {
        let desc = CATapDescription(monoGlobalTapButExcludeProcesses: [])
        desc.name = "Huddle system audio"
        desc.isPrivate = true
        desc.muteBehavior = .unmuted
        var err = AudioHardwareCreateProcessTap(desc, &tapID)
        guard err == noErr else { throw TapError("could not create the system audio tap (\(err))") }

        var fmtAddr = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var fmtSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        err = AudioObjectGetPropertyData(tapID, &fmtAddr, 0, nil, &fmtSize, &format)
        guard err == noErr else { throw TapError("could not read the tap format (\(err))") }
        guard format.mFormatID == kAudioFormatLinearPCM, format.mFormatFlags & kAudioFormatFlagIsFloat != 0, format.mBitsPerChannel == 32 else {
            throw TapError("unexpected tap format \(format.mFormatID)/\(format.mFormatFlags)/\(format.mBitsPerChannel)")
        }
        deviceRate = format.mSampleRate

        let outUID = try defaultOutputUID()
        let aggDesc: [String: Any] = [
            kAudioAggregateDeviceNameKey as String: "Huddle System Audio",
            kAudioAggregateDeviceUIDKey as String: "com.huddle.desktop.tap." + UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey as String: outUID,
            kAudioAggregateDeviceIsPrivateKey as String: true,
            kAudioAggregateDeviceIsStackedKey as String: false,
            kAudioAggregateDeviceTapAutoStartKey as String: true,
            kAudioAggregateDeviceSubDeviceListKey as String: [[kAudioSubDeviceUIDKey as String: outUID]],
            kAudioAggregateDeviceTapListKey as String: [[kAudioSubTapDriftCompensationKey as String: true, kAudioSubTapUIDKey as String: desc.uuid.uuidString]],
        ]
        err = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &aggID)
        guard err == noErr else { throw TapError("could not create the capture device (\(err))") }

        if let outPath { writer = try WavWriter(path: outPath) }
        err = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, nil) { [unowned self] _, inData, _, _, _ in
            self.handle(inData)
        }
        guard err == noErr, procID != nil else { throw TapError("could not attach to the capture device (\(err))") }
        err = AudioDeviceStart(aggID, procID)
        guard err == noErr else { throw TapError("could not start the capture device (\(err))") }
        readDeviceRate()
        var rateAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        AudioObjectAddPropertyListenerBlock(aggID, &rateAddr, DispatchQueue.global()) { [weak self] _, _ in self?.readDeviceRate() }
        if debug { FileHandle.standardError.write("tap format \(format.mSampleRate) Hz \(format.mChannelsPerFrame) ch flags \(format.mFormatFlags); device rate \(deviceRate) Hz\n".data(using: .utf8)!) }
    }

    func stop() {
        if let procID { AudioDeviceStop(aggID, procID); AudioDeviceDestroyIOProcID(aggID, procID) }
        if aggID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggID) }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
        writer?.close()
    }

    /// The aggregate's nominal rate is what the callbacks are clocked by.
    func readDeviceRate() {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var rate: Float64 = 0
        var size = UInt32(MemoryLayout<Float64>.size)
        if AudioObjectGetPropertyData(aggID, &addr, 0, nil, &size, &rate) == noErr, rate > 1000, rate != deviceRate {
            if debug { FileHandle.standardError.write("device rate \(deviceRate) → \(rate) Hz\n".data(using: .utf8)!) }
            deviceRate = rate
        }
    }

    private func defaultOutputUID() throws -> String {
        var devID = AudioObjectID(kAudioObjectUnknown)
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var err = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &devID)
        guard err == noErr, devID != kAudioObjectUnknown else { throw TapError("no default output device (\(err))") }
        var uidAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDeviceUID, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var uid: Unmanaged<CFString>? = nil
        var uidSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        err = AudioObjectGetPropertyData(devID, &uidAddr, 0, nil, &uidSize, &uid)
        guard err == noErr, let s = uid?.takeUnretainedValue() else { throw TapError("no output device id (\(err))") }
        return s as String
    }

    /// Mix the tap's float buffers down to 16-bit mono at 48 kHz and report the peak level.
    private func handle(_ list: UnsafePointer<AudioBufferList>) {
        let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: list))
        guard abl.count > 0, let first = abl[0].mData else { return }
        let channels = Int(max(1, format.mChannelsPerFrame))
        let nonInterleaved = format.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
        let frames = nonInterleaved ? Int(abl[0].mDataByteSize) / 4 : Int(abl[0].mDataByteSize) / (4 * channels)
        guard frames > 0 else { return }
        var mono = [Float](repeating: 0, count: frames)
        var peak: Float = 0
        if nonInterleaved {
            let ptrs = (0..<min(channels, abl.count)).compactMap { abl[$0].mData?.assumingMemoryBound(to: Float.self) }
            for f in 0..<frames {
                var acc: Float = 0
                for p in ptrs { acc += p[f] }
                let v = max(-1, min(1, acc / Float(max(1, ptrs.count))))
                peak = max(peak, abs(v)); mono[f] = v
            }
        } else {
            let p = first.assumingMemoryBound(to: Float.self)
            for f in 0..<frames {
                var acc: Float = 0
                for c in 0..<channels { acc += p[f * channels + c] }
                let v = max(-1, min(1, acc / Float(channels)))
                peak = max(peak, abs(v)); mono[f] = v
            }
        }
        maxAbs = max(maxAbs, peak)
        callbacks += 1
        if callbacks % 100 == 0 { readDeviceRate() }  // belt and braces next to the listener
        if writer != nil {
            let out: [Int16]
            let ratio = deviceRate / sampleRate
            if abs(ratio - 1) < 1e-9 {
                out = mono.map { Int16($0 * 32767) }
            } else {
                // Linear interpolation from the device rate to 48 kHz; `prev`/`phase` carry the
                // position across callbacks so the stream stays continuous.
                let x = [prev] + mono
                var o = [Int16](); o.reserveCapacity(Int(Double(frames) / ratio) + 2)
                var p = phase
                while p < Double(frames) {
                    let i = Int(p); let f = Float(p - Double(i))
                    let v = x[i] + (x[i + 1] - x[i]) * f
                    o.append(Int16(max(-1, min(1, v)) * 32767))
                    p += ratio
                }
                phase = p - Double(frames)
                out = o
            }
            writer?.append(Data(bytes: out, count: out.count * 2))
        }
        prev = mono[frames - 1]
        // Level lines are for the recorder's meter; the probe's stdout must stay a single word.
        if writer != nil, Date().timeIntervalSince(lastLevel) > 0.2 {
            lastLevel = Date()
            print("level \(peak)"); fflush(stdout)
        }
    }
}

/// A 1 kHz tone at −90 dBFS on the default output: inaudible, but non-zero to the tap.
final class ProbeTone {
    let engine = AVAudioEngine()
    init() {
        var phase: Float = 0
        let step = Float(2 * Double.pi * 1000 / 48000)
        let node = AVAudioSourceNode { _, _, frameCount, abl -> OSStatus in
            let buffers = UnsafeMutableAudioBufferListPointer(abl)
            for f in 0..<Int(frameCount) {
                let v = sin(phase) * 0.00003
                phase += step
                for b in buffers { b.mData!.assumingMemoryBound(to: Float.self)[f] = v }
            }
            return noErr
        }
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1))
        engine.connect(engine.mainMixerNode, to: engine.outputNode, format: nil)
    }
    func start() { try? engine.start() }
    func stop() { engine.stop() }
}

/// "granted" when the tap hears anything within 0.5 s (our own inaudible tone included), otherwise
/// "denied" — which is also what an unanswered permission dialog looks like; the app re-checks.
@available(macOS 14.2, *)
func probe() -> Never {
    let rec = TapRecorder()
    do { try rec.start(outPath: nil) } catch { print("denied"); exit(0) }
    let tone = ProbeTone()
    tone.start()
    usleep(500_000)
    let heard = rec.maxAbs > 0.000001
    tone.stop()
    rec.stop()
    print(heard ? "granted" : "denied"); exit(0)
}

/// Creating a tap is what makes macOS show the "System Audio Recording Only" prompt; it appears
/// only while the permission is undetermined, so this is safe to call on every launch.
@available(macOS 14.2, *)
func requestPermission() -> Never {
    let rec = TapRecorder()
    if (try? rec.start(outPath: nil)) != nil {
        usleep(200_000)
        rec.stop()
    }
    print("unknown"); exit(0)
}

@available(macOS 14.2, *)
func runRecording(outPath: String) -> Never {
    let rec = TapRecorder()
    do { try rec.start(outPath: outPath) } catch { fail("start failed: \(error)", code: 3) }
    print("READY"); fflush(stdout)
    signal(SIGTERM) { _ in exit(0) }
    signal(SIGINT) { _ in exit(0) }
    DispatchQueue.global().async {
        // Run until stdin closes (parent dropped the pipe) or "stop" arrives.
        while let line = readLine() { if line == "stop" { break } }
        rec.stop()
        exit(0)
    }
    RunLoop.main.run()
    exit(0)
}

// MARK: - Commands

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: huddle-audio-tap check|request|mic-check|mic-request|record <out.wav>", code: 64) }
guard #available(macOS 14.2, *) else { fail("System audio capture needs macOS 14.2 or newer.", code: 5) }

switch args[1] {
case "check":
    probe()
case "request":
    requestPermission()
case "mic-check":
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: print("granted")
    case .notDetermined: print("undetermined")
    default: print("denied")
    }
    exit(0)
case "mic-request":
    let sem = DispatchSemaphore(value: 0)
    var ok = false
    AVCaptureDevice.requestAccess(for: .audio) { granted in ok = granted; sem.signal() }
    _ = sem.wait(timeout: .now() + 120)
    print(ok ? "granted" : "denied"); exit(0)
case "record":
    guard args.count >= 3 else { fail("missing output path", code: 64) }
    runRecording(outPath: args[2])
default:
    fail("unknown command", code: 64)
}
