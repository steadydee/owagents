import Foundation
import Security

// The secret never appears in argv, source, logs, or shell expansion.
let service = "com.owlswatch.agent-recovery"
let account = "runtime-recovery-v1"
let command = CommandLine.arguments.dropFirst().first ?? ""
guard ["ensure", "read"].contains(command) else { exit(2) }
let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
  kSecAttrService as String: service, kSecAttrAccount as String: account,
  kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
var item: CFTypeRef?
var status = SecItemCopyMatching(query as CFDictionary, &item)
if status == errSecItemNotFound && command == "ensure" {
  var bytes = [UInt8](repeating: 0, count: 32)
  guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { exit(3) }
  let data = Data(bytes)
  let attributes: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: service, kSecAttrAccount as String: account,
    kSecAttrLabel as String: "Owl's Watch encrypted agent recovery",
    kSecValueData as String: data]
  status = SecItemAdd(attributes as CFDictionary, nil)
  guard status == errSecSuccess else {
    FileHandle.standardError.write(Data("KEYCHAIN_CREATE_FAILED:\(status)\n".utf8)); exit(4)
  }
  print("created")
} else {
  guard status == errSecSuccess, let data = item as? Data, data.count == 32 else {
    FileHandle.standardError.write(Data("KEYCHAIN_READ_FAILED:\(status)\n".utf8)); exit(5)
  }
  if command == "ensure" { print("present") }
  else { FileHandle.standardOutput.write(data) }
}
