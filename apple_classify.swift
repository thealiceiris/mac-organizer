// Local-only bridge between the Python router and Apple's on-device model.
import Foundation
import FoundationModels

@Generable
struct FilingDecision {
    @Guide(description: "Exactly one candidate folder name, or unsure")
    var folder: String
    @Guide(description: "Confidence from 0 to 1", .range(0.0...1.0))
    var confidence: Double
    @Guide(description: "One short sentence explaining the choice")
    var reason: String
    @Guide(description: "A short topic suitable for a folder name when unsure")
    var topic: String
}

struct Message: Decodable {
    let role: String
    let content: String
}

struct Request: Decodable {
    let messages: [Message]
}

@main
struct AppleClassifier {
    static func fail(_ message: String, code: Int32) -> Never {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(code)
    }

    static func main() async {
        let model = SystemLanguageModel.default
        guard case .available = model.availability else {
            fail("Apple Intelligence unavailable: \(model.availability). Enable Apple Intelligence in System Settings and wait for its model to finish downloading.", code: 2)
        }
        if CommandLine.arguments.contains("--check") {
            print("Apple Intelligence on-device model available")
            return
        }
        do {
            let request = try JSONDecoder().decode(Request.self, from: FileHandle.standardInput.readDataToEndOfFile())
            let instructions = request.messages.filter { $0.role == "system" }.map(\.content).joined(separator: "\n")
            let prompt = request.messages.filter { $0.role == "user" }.map(\.content).joined(separator: "\n")
            let session = LanguageModelSession(model: model, instructions: instructions)
            let response = try await session.respond(to: prompt, generating: FilingDecision.self,
                                                     options: GenerationOptions(sampling: .greedy,
                                                                                maximumResponseTokens: 256))
            let decision = response.content
            let result: [String: Any] = ["folder": decision.folder, "confidence": decision.confidence,
                                         "reason": decision.reason, "topic": decision.topic]
            let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        } catch {
            // Refusals and context overflows affect this item, not the whole run.
            fail("Apple Intelligence could not classify this item: \(error)", code: 3)
        }
    }
}
