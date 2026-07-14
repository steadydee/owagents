import AppKit
import Foundation
import Vision

struct Output: Encodable {
    let ok: Bool
    let lines: [String]
    let error: String?
}

func emit(_ output: Output) {
    let encoder = JSONEncoder()
    if let data = try? encoder.encode(output), let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        print("{\"ok\":false,\"lines\":[],\"error\":\"encode_failed\"}")
    }
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    emit(Output(ok: false, lines: [], error: "missing_path"))
    exit(1)
}

let url = URL(fileURLWithPath: args[1])
guard let image = NSImage(contentsOf: url),
      let tiff = image.tiffRepresentation,
      let ciImage = CIImage(data: tiff) else {
    emit(Output(ok: false, lines: [], error: "image_load_failed"))
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US", "es-ES"]
request.minimumTextHeight = 0.01

let handler = VNImageRequestHandler(ciImage: ciImage, options: [:])
do {
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { observation in
        observation.topCandidates(1).first?.string
    }.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    emit(Output(ok: true, lines: lines, error: nil))
} catch {
    emit(Output(ok: false, lines: [], error: "ocr_failed"))
    exit(1)
}
